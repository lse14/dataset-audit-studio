from __future__ import annotations

import mimetypes
import os
import stat
import string
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from PIL import Image, ImageOps
from sqlalchemy import select

from dataset_audit_studio.database.models import Sample, Task
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import TaskDomainError, TaskNotFound
from dataset_audit_studio.latent.common import source_is_unchanged
from dataset_audit_studio.latent.types import LatentSample
from dataset_audit_studio.workspace.types import DirectoryEntryView, DirectoryListingView


def _is_reparse(path: Path) -> bool:
    try:
        result = path.lstat()
    except OSError:
        return False
    attributes = getattr(result, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


class MediaAccessError(TaskDomainError):
    pass


@dataclass(frozen=True)
class SampleMedia:
    path: Path
    media_type: str


class WorkspaceFileAccess:
    def __init__(self, database: Database, *, project_root: Path) -> None:
        self.database = database
        self.project_root = project_root.resolve(strict=False)

    def thumbnail(self, task_id: str, sample_id: str, *, size: int) -> Path:
        with self.database.read_session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(f"Task not found: {task_id}")
            sample = session.scalar(
                select(Sample).where(Sample.task_id == task_id, Sample.id == sample_id)
            )
            if sample is None:
                raise TaskNotFound(f"Sample not found for task: {sample_id}")
            values = {
                "relative_path": sample.relative_path,
                "source_size": sample.source_size,
                "source_mtime_ns": sample.source_mtime_ns,
                "source_sha256": sample.source_sha256,
                "pixel_sha256": sample.pixel_sha256 or sample.source_sha256,
                "extracted_frame_path": sample.extracted_frame_path,
            }
            source_root = Path(task.source_root)
        cache = self._thumbnail_path(
            task_id,
            sample_id,
            values["pixel_sha256"],
            size,
        )
        if cache.is_file():
            return cache
        source = source_root.joinpath(*Path(values["relative_path"]).parts).resolve(strict=True)
        source.relative_to(source_root.resolve(strict=True))
        unchanged = LatentSample(
            sample_id=sample_id,
            relative_path=values["relative_path"],
            source_path=source,
            source_size=values["source_size"],
            source_mtime_ns=values["source_mtime_ns"],
            source_sha256=values["source_sha256"],
            export_requires_render=False,
        )
        if not source_is_unchanged(unchanged):
            raise RuntimeError("Source image changed after scanning")
        if values["extracted_frame_path"]:
            image_path = self.project_root.joinpath(
                *Path(values["extracted_frame_path"]).parts
            ).resolve(strict=True)
            image_path.relative_to(self.project_root)
        else:
            image_path = source
        self._render_thumbnail(image_path, cache, size)
        return cache

    def media(self, task_id: str, sample_id: str) -> SampleMedia:
        with self.database.read_session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(f"Task not found: {task_id}")
            sample = session.scalar(
                select(Sample).where(Sample.task_id == task_id, Sample.id == sample_id)
            )
            if sample is None:
                raise TaskNotFound(f"Sample not found for task: {sample_id}")
            values = {
                "relative_path": sample.relative_path,
                "source_size": sample.source_size,
                "source_mtime_ns": sample.source_mtime_ns,
                "source_sha256": sample.source_sha256,
                "extracted_frame_path": sample.extracted_frame_path,
                "media_kind": sample.media_kind,
            }
            source_root = Path(task.source_root)
        try:
            source = self._source_path(sample_id, source_root, values)
            if values["media_kind"] == "image":
                media_path = source
            else:
                extracted = values["extracted_frame_path"]
                if not isinstance(extracted, str):
                    raise MediaAccessError("Sample media is unavailable")
                media_path = self._contained_file(self.project_root, extracted)
            media_type = mimetypes.guess_type(media_path.name)[0]
            if media_type in (None, "") and media_path.suffix.casefold() == ".webp":
                media_type = "image/webp"
            if not isinstance(media_type, str) or not media_type.startswith("image/"):
                raise MediaAccessError("Sample media is unavailable")
            return SampleMedia(path=media_path, media_type=media_type)
        except MediaAccessError:
            raise
        except (OSError, ValueError):
            raise MediaAccessError("Sample media is unavailable") from None

    @staticmethod
    def _contained_file(root: Path, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
            raise ValueError("unsafe sample identity")
        posix_path = PurePosixPath(relative_path)
        windows_path = PureWindowsPath(relative_path)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or not posix_path.parts
            or any(part in {".", ".."} for part in posix_path.parts)
        ):
            raise ValueError("unsafe sample identity")
        resolved_root = root.resolve(strict=True)
        candidate = resolved_root.joinpath(*posix_path.parts).resolve(strict=True)
        candidate.relative_to(resolved_root)
        if not candidate.is_file():
            raise ValueError("sample media is not a file")
        return candidate

    def _source_path(
        self,
        sample_id: str,
        source_root: Path,
        values: dict[str, object],
    ) -> Path:
        source = self._contained_file(source_root, str(values["relative_path"]))
        unchanged = LatentSample(
            sample_id=sample_id,
            relative_path=str(values["relative_path"]),
            source_path=source,
            source_size=int(values["source_size"]),
            source_mtime_ns=int(values["source_mtime_ns"]),
            source_sha256=str(values["source_sha256"]),
            export_requires_render=False,
        )
        if not source_is_unchanged(unchanged):
            raise MediaAccessError("Sample media identity no longer matches the scanned source")
        return source

    def directories(self, raw_path: str | None) -> DirectoryListingView:
        if raw_path is None or not raw_path.strip():
            roots = self._filesystem_roots()
            return DirectoryListingView(
                current=None,
                parent=None,
                entries=tuple(DirectoryEntryView(name=str(path), path=str(path)) for path in roots),
            )
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Directory browser path must be absolute")
        current = candidate.resolve(strict=True)
        if not current.is_dir() or _is_reparse(current):
            raise ValueError("Directory browser path must be a regular directory")
        entries: list[DirectoryEntryView] = []
        try:
            children = sorted(current.iterdir(), key=lambda path: path.name.casefold())
        except OSError as error:
            raise ValueError(f"Directory is not readable: {error}") from error
        for child in children:
            try:
                if child.is_dir() and not _is_reparse(child):
                    entries.append(DirectoryEntryView(name=child.name, path=str(child)))
            except OSError:
                continue
        parent = None if current.parent == current else str(current.parent)
        return DirectoryListingView(
            current=str(current),
            parent=parent,
            entries=tuple(entries),
        )

    def _thumbnail_path(
        self,
        task_id: str,
        sample_id: str,
        pixel_sha256: str,
        size: int,
    ) -> Path:
        safe = set(string.ascii_letters + string.digits + "-_")
        if any(character not in safe for character in task_id + sample_id):
            raise ValueError("Unsafe thumbnail identity")
        path = (
            self.project_root
            / "data"
            / "tasks"
            / task_id
            / "thumbnails"
            / sample_id
            / f"{pixel_sha256}-{size}.jpg"
        ).resolve(strict=False)
        path.relative_to(self.project_root)
        return path

    @staticmethod
    def _render_thumbnail(source: Path, destination: Path, size: int) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_suffix(".jpg.part")
        try:
            with Image.open(source) as opened:
                opened.seek(0)
                frame = ImageOps.exif_transpose(opened)
                rgba = frame.convert("RGBA")
                rendered = Image.new("RGB", rgba.size, "white")
                rendered.paste(rgba, mask=rgba.getchannel("A"))
                rendered.thumbnail((size, size), Image.Resampling.LANCZOS)
                with part.open("xb") as stream:
                    rendered.save(
                        stream,
                        format="JPEG",
                        quality=84,
                        optimize=True,
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
            os.replace(part, destination)
        finally:
            part.unlink(missing_ok=True)

    @staticmethod
    def _filesystem_roots() -> tuple[Path, ...]:
        if os.name != "nt":
            return (Path("/"),)
        return tuple(
            path for letter in string.ascii_uppercase if (path := Path(f"{letter}:\\")).is_dir()
        )
