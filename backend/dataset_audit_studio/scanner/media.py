from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import av
import pillow_avif  # noqa: F401 - registers AVIF with Pillow
from PIL import Image, ImageFile, ImageOps

from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.discovery import VIDEO_EXTENSIONS
from dataset_audit_studio.scanner.types import DiscoveredMedia

ImageFile.LOAD_TRUNCATED_IMAGES = False
EXIF_ORIENTATION = 274


class MediaDecodeError(RuntimeError):
    def __init__(self, code: str, detail: str, *, source_sha256: str | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.source_sha256 = source_sha256


@dataclass
class DecodedMedia:
    image: Image.Image
    source_sha256: str
    media_kind: str
    encoded_width: int
    encoded_height: int
    frame_count: int | None
    is_animated: bool
    exif_orientation: int | None
    extracted_frame_path: str | None
    export_requires_render: bool
    source_changed: bool

    def close(self) -> None:
        self.image.close()


def hash_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _stat_changed(item: DiscoveredMedia, before: os.stat_result, after: os.stat_result) -> bool:
    return any(
        (
            before.st_size != item.source_size,
            before.st_mtime_ns != item.source_mtime_ns,
            after.st_size != before.st_size,
            after.st_mtime_ns != before.st_mtime_ns,
        )
    )


def _pixel_limit(width: int, height: int, config: ScanConfig) -> None:
    if width <= 0 or height <= 0:
        raise MediaDecodeError("invalid_dimensions", f"Invalid dimensions: {width}x{height}")
    if width * height > config.max_decode_pixels:
        raise MediaDecodeError(
            "pixel_limit_exceeded",
            f"Image has {width * height} pixels; limit is {config.max_decode_pixels}",
        )


def _first_pillow_frame(source: Image.Image) -> Image.Image:
    frame_count = int(getattr(source, "n_frames", 1) or 1)
    last_error: Exception | None = None
    for frame_index in range(frame_count):
        try:
            source.seek(frame_index)
            return source.convert("RGBA").copy()
        except (EOFError, OSError, ValueError) as error:
            last_error = error
    raise MediaDecodeError("no_decodable_frame", str(last_error or "No image frame"))


def _write_extracted_frame(
    image: Image.Image,
    root: Path,
    source_sha256: str,
    *,
    project_root: Path,
) -> str:
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{source_sha256}.png"
    if not destination.is_file():
        part = destination.with_suffix(".png.part")
        image.save(part, format="PNG", optimize=False)
        os.replace(part, destination)
    return destination.relative_to(project_root.resolve(strict=False)).as_posix()


def _decode_pillow(
    item: DiscoveredMedia,
    config: ScanConfig,
    extracted_root: Path,
    source_sha256: str,
    before: os.stat_result,
    project_root: Path,
) -> DecodedMedia:
    Image.MAX_IMAGE_PIXELS = config.max_decode_pixels
    try:
        with Image.open(item.absolute_path) as source:
            encoded_width, encoded_height = source.size
            _pixel_limit(encoded_width, encoded_height, config)
            frame_count = int(getattr(source, "n_frames", 1) or 1)
            extension = item.absolute_path.suffix.casefold()
            is_animated = bool(getattr(source, "is_animated", False) and frame_count > 1)
            requires_render = extension == ".gif" or (extension == ".webp" and is_animated)
            orientation = None
            try:
                orientation = int(source.getexif().get(EXIF_ORIENTATION, 1))
            except (AttributeError, TypeError, ValueError):
                orientation = None
            if orientation is not None and not 1 <= orientation <= 8:
                orientation = None
            if requires_render:
                image = _first_pillow_frame(source)
                media_kind = "animation"
            else:
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGBA").copy()
                media_kind = "image"
    except MediaDecodeError:
        raise
    except Exception as error:
        raise MediaDecodeError("decode_error", f"{type(error).__name__}: {error}") from error

    extracted = (
        _write_extracted_frame(
            image,
            extracted_root,
            source_sha256,
            project_root=project_root,
        )
        if requires_render
        else None
    )
    after = item.absolute_path.stat()
    return DecodedMedia(
        image=image,
        source_sha256=source_sha256,
        media_kind=media_kind,
        encoded_width=encoded_width,
        encoded_height=encoded_height,
        frame_count=frame_count,
        is_animated=is_animated or extension == ".gif",
        exif_orientation=orientation,
        extracted_frame_path=extracted,
        export_requires_render=requires_render,
        source_changed=_stat_changed(item, before, after),
    )


def _video_rotation(stream: av.video.stream.VideoStream) -> int:
    raw = stream.metadata.get("rotate", "0")
    try:
        return int(float(raw)) % 360
    except (TypeError, ValueError):
        return 0


def _decode_video(
    item: DiscoveredMedia,
    config: ScanConfig,
    extracted_root: Path,
    source_sha256: str,
    before: os.stat_result,
    project_root: Path,
) -> DecodedMedia:
    try:
        with av.open(str(item.absolute_path), mode="r") as container:
            streams = list(container.streams.video)
            if not streams:
                raise MediaDecodeError("no_video_stream", "Container has no video stream")
            stream = streams[0]
            encoded_width = int(stream.codec_context.width)
            encoded_height = int(stream.codec_context.height)
            _pixel_limit(encoded_width, encoded_height, config)
            frame_count = int(stream.frames) if stream.frames else None
            frame = next(container.decode(video=0), None)
            if frame is None:
                raise MediaDecodeError("no_decodable_frame", "Video has no decodable frame")
            image = frame.to_image().convert("RGBA")
            rotation = _video_rotation(stream)
            if rotation:
                image = image.rotate(-rotation, expand=True)
    except MediaDecodeError:
        raise
    except Exception as error:
        raise MediaDecodeError("decode_error", f"{type(error).__name__}: {error}") from error

    extracted = _write_extracted_frame(
        image,
        extracted_root,
        source_sha256,
        project_root=project_root,
    )
    after = item.absolute_path.stat()
    return DecodedMedia(
        image=image,
        source_sha256=source_sha256,
        media_kind="video",
        encoded_width=encoded_width,
        encoded_height=encoded_height,
        frame_count=frame_count,
        is_animated=True,
        exif_orientation=None,
        extracted_frame_path=extracted,
        export_requires_render=True,
        source_changed=_stat_changed(item, before, after),
    )


def decode_media(
    item: DiscoveredMedia,
    config: ScanConfig,
    *,
    extracted_root: Path,
    project_root: Path | None = None,
) -> DecodedMedia:
    project_root = (project_root or PROJECT_ROOT).resolve(strict=False)
    try:
        before = item.absolute_path.stat()
        source_sha256 = hash_file(item.absolute_path)
    except OSError as error:
        raise MediaDecodeError("read_error", f"{type(error).__name__}: {error}") from error

    if _stat_changed(item, before, before):
        raise MediaDecodeError(
            "source_changed",
            "File metadata changed after manifest creation",
            source_sha256=source_sha256,
        )
    try:
        if item.absolute_path.suffix.casefold() in VIDEO_EXTENSIONS:
            return _decode_video(
                item,
                config,
                extracted_root,
                source_sha256,
                before,
                project_root,
            )
        return _decode_pillow(
            item,
            config,
            extracted_root,
            source_sha256,
            before,
            project_root,
        )
    except MediaDecodeError as error:
        if error.source_sha256 is None:
            error.source_sha256 = source_sha256
        raise
