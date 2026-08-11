from __future__ import annotations

import hashlib
import os
from pathlib import Path

import av
import numpy as np
import pytest
from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.discovery import discover_media
from dataset_audit_studio.scanner.media import MediaDecodeError, decode_media
from dataset_audit_studio.scanner.metrics import pixel_sha256
from PIL import Image, ImageOps


def _item(path: Path):
    result = discover_media(path.parent, ScanConfig())
    return next(item for item in result.items if item.absolute_path == path)


def test_static_image_sha_and_exif_display_orientation(tmp_path: Path) -> None:
    source = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (20, 30), (10, 40, 220))
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, quality=95, exif=exif)
    expected_source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with Image.open(source) as opened:
        expected_display = ImageOps.exif_transpose(opened).convert("RGBA")
        expected_pixel_sha = pixel_sha256(expected_display)

    decoded = decode_media(_item(source), ScanConfig(), extracted_root=tmp_path / "frames")
    try:
        assert decoded.source_sha256 == expected_source_sha
        assert (decoded.encoded_width, decoded.encoded_height) == (20, 30)
        assert decoded.image.size == (30, 20)
        assert decoded.exif_orientation == 6
        assert pixel_sha256(decoded.image) == expected_pixel_sha
        assert decoded.export_requires_render is False
        assert decoded.extracted_frame_path is None
    finally:
        decoded.close()


@pytest.mark.parametrize("orientation", (0, 9))
def test_out_of_range_exif_orientation_is_recorded_as_unknown(
    tmp_path: Path, orientation: int
) -> None:
    source = tmp_path / f"orientation-{orientation}.jpg"
    image = Image.new("RGB", (20, 30), (10, 40, 220))
    exif = Image.Exif()
    exif[274] = orientation
    image.save(source, quality=95, exif=exif)

    decoded = decode_media(_item(source), ScanConfig(), extracted_root=tmp_path / "frames")
    try:
        assert decoded.exif_orientation is None
    finally:
        decoded.close()


def test_gif_and_animated_webp_use_first_frame(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    import dataset_audit_studio.scanner.media as media_module

    monkeypatch.setattr(media_module, "PROJECT_ROOT", project)
    source = tmp_path / "source"
    source.mkdir()
    red = Image.new("RGBA", (18, 12), (255, 0, 0, 255))
    blue = Image.new("RGBA", (18, 12), (0, 0, 255, 255))
    gif = source / "animated.gif"
    red.save(gif, save_all=True, append_images=[blue], duration=50, loop=0)
    webp = source / "animated.webp"
    try:
        red.save(webp, save_all=True, append_images=[blue], duration=50, loop=0, lossless=True)
    except OSError as error:
        pytest.skip(f"Animated WebP encoding unavailable: {error}")

    for path in (gif, webp):
        decoded = decode_media(
            _item(path),
            ScanConfig(),
            extracted_root=project / "data" / "frames",
        )
        try:
            assert decoded.frame_count == 2
            assert decoded.is_animated is True
            assert decoded.media_kind == "animation"
            assert decoded.export_requires_render is True
            assert decoded.image.getpixel((0, 0))[:3] == (255, 0, 0)
            extracted = project.joinpath(*Path(decoded.extracted_frame_path or "").parts)
            assert extracted.is_file()
            with Image.open(extracted) as frame:
                assert frame.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
        finally:
            decoded.close()


def test_video_uses_first_decodable_frame(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    import dataset_audit_studio.scanner.media as media_module

    monkeypatch.setattr(media_module, "PROJECT_ROOT", project)
    source = tmp_path / "clip.mp4"
    with av.open(str(source), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=2)
        stream.width = 32
        stream.height = 24
        stream.pix_fmt = "yuv420p"
        for color in ((230, 20, 10), (10, 20, 230)):
            array = np.empty((24, 32, 3), dtype=np.uint8)
            array[:, :] = color
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    decoded = decode_media(
        _item(source),
        ScanConfig(),
        extracted_root=project / "data" / "frames",
    )
    try:
        red, green, blue, _ = decoded.image.getpixel((10, 10))
        assert red > 180 and green < 60 and blue < 60
        assert decoded.media_kind == "video"
        assert decoded.is_animated is True
        assert decoded.export_requires_render is True
        assert decoded.extracted_frame_path is not None
    finally:
        decoded.close()


def test_avif_static_decode_when_encoder_is_available(tmp_path: Path) -> None:
    source = tmp_path / "sample.avif"
    try:
        Image.new("RGB", (22, 14), (40, 180, 70)).save(source)
    except (KeyError, OSError) as error:
        pytest.skip(f"AVIF encoder unavailable: {error}")

    decoded = decode_media(_item(source), ScanConfig(), extracted_root=tmp_path / "frames")
    try:
        assert decoded.image.size == (22, 14)
        assert decoded.media_kind == "image"
        assert decoded.export_requires_render is False
    finally:
        decoded.close()


def test_corrupt_and_manifest_changed_files_are_reported(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(MediaDecodeError) as corrupt_error:
        decode_media(_item(corrupt), ScanConfig(), extracted_root=tmp_path / "frames")
    assert corrupt_error.value.code == "decode_error"
    assert corrupt_error.value.source_sha256 == hashlib.sha256(corrupt.read_bytes()).hexdigest()

    changed = tmp_path / "changed.png"
    Image.new("RGB", (16, 16), "red").save(changed)
    stale_item = _item(changed)
    Image.new("RGB", (17, 16), "blue").save(changed)
    current = changed.stat()
    os.utime(
        changed,
        ns=(current.st_atime_ns, max(current.st_mtime_ns, stale_item.source_mtime_ns + 1_000_000)),
    )
    with pytest.raises(MediaDecodeError) as changed_error:
        decode_media(stale_item, ScanConfig(), extracted_root=tmp_path / "frames")
    assert changed_error.value.code == "source_changed"
