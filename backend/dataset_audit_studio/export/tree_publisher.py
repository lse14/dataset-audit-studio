from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from dataset_audit_studio.components.dataset_export.contracts import (
    ExportPlan,
    PlannedFile,
)
from dataset_audit_studio.core.file_integrity import is_reparse, sha256_file
from dataset_audit_studio.export.image_conversion import encode_export_image

__all__ = ("ExportTreePublisher",)

_REPLACE_RETRY_ATTEMPTS = 11
_REPLACE_RETRY_DELAY_SECONDS = 0.1
_RETRYABLE_REPLACE_WINERRORS = frozenset((32, 33))
_T = TypeVar("_T")


def _is_retryable_windows_lock(error: BaseException) -> bool:
    return (
        isinstance(error, OSError)
        and getattr(error, "winerror", None) in _RETRYABLE_REPLACE_WINERRORS
    )


def _retry_windows_lock(operation: Callable[[], _T]) -> _T:
    for attempt in range(_REPLACE_RETRY_ATTEMPTS):
        try:
            return operation()
        except OSError as error:
            if not _is_retryable_windows_lock(error) or attempt == _REPLACE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_RETRY_DELAY_SECONDS * (attempt + 1))
    raise AssertionError("unreachable retry loop")


class ExportTreePublisher:
    """Publish one already-planned copy-export tree without task state ownership."""

    def validate_roots(self, source_root: Path, output_root: Path) -> None:
        if not output_root.name:
            raise ValueError("Export output_root cannot be a filesystem root")
        for child, parent, detail in (
            (output_root, source_root, "inside source_root"),
            (source_root, output_root, "a parent of source_root"),
        ):
            try:
                child.relative_to(parent)
            except ValueError:
                continue
            raise ValueError(f"Export output_root cannot be {detail}")

    def prepare_directories(
        self,
        output_root: Path,
        staging_root: Path,
        plan: ExportPlan,
        *,
        refuse_nonempty: bool,
    ) -> None:
        output_root.parent.mkdir(parents=True, exist_ok=True)
        if output_root.exists():
            if not output_root.is_dir():
                raise FileExistsError(f"Export output is not a directory: {output_root}")
            if refuse_nonempty and any(output_root.iterdir()):
                raise FileExistsError(f"Export output is not empty: {output_root}")
        required = sum(file.size_bytes for file in plan.files)
        free = shutil.disk_usage(output_root.parent).free
        if free < required:
            raise OSError(f"Export requires {required} bytes, only {free} bytes are free")
        if staging_root.exists():
            if is_reparse(staging_root) or not staging_root.is_dir():
                raise FileExistsError(f"Export staging path is unsafe: {staging_root}")
            if any(staging_root.iterdir()):
                raise FileExistsError(f"Export staging path is not empty: {staging_root}")
        else:
            staging_root.mkdir()
        for dataset in plan.datasets:
            staging_root.joinpath(*Path(dataset.relative_root).parts).mkdir(
                parents=True,
                exist_ok=True,
            )

    def assert_staging_ready(self, output_root: Path, staging_root: Path) -> None:
        if not staging_root.is_dir():
            raise RuntimeError("Export staging directory is missing")
        if output_root.exists() and (
            not output_root.is_dir() or any(output_root.iterdir())
        ):
            raise FileExistsError(f"Export output became non-empty: {output_root}")

    def write_file(self, staging_root: Path, file: PlannedFile) -> None:
        destination = self._destination(staging_root, file.destination_relative)
        if destination.exists():
            _retry_windows_lock(lambda: self._verify_path(destination, file))
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        current = destination.parent
        while current != staging_root:
            if is_reparse(current):
                raise RuntimeError(f"Export staging contains a reparse directory: {current}")
            current = current.parent
        part = destination.with_name(f"{destination.name}.part")
        if part.exists() and (
            _retry_windows_lock(lambda: is_reparse(part))
            or not _retry_windows_lock(lambda: part.is_file())
        ):
            raise RuntimeError(f"Export partial path is unsafe: {part}")
        failure: BaseException | None = None
        try:
            if file.transcode_format is not None:
                if file.source_path is None:
                    raise RuntimeError(
                        f"Export transcode source is missing: {file.destination_relative}"
                    )
                self._write_content_file(
                    part, encode_export_image(file.source_path, file.transcode_format)
                )
            elif file.source_path is not None:
                self._copy_source_file(file.source_path, part)
            else:
                self._write_content_file(part, file.content or b"")
            _retry_windows_lock(lambda: self._verify_path(part, file))
            self._publish_part(part, destination)
            _retry_windows_lock(lambda: self._verify_path(destination, file))
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                _retry_windows_lock(lambda: part.unlink(missing_ok=True))
            except BaseException as cleanup_error:
                if failure is None:
                    raise
                failure.add_note(
                    f"Unable to remove temporary export file {part}: {cleanup_error}"
                )

    def verify_file(self, root: Path, file: PlannedFile) -> None:
        self._verify_path(self._destination(root, file.destination_relative), file)

    def verify_tree_layout(self, root: Path, plan: ExportPlan) -> None:
        expected = {file.destination_relative.casefold() for file in plan.files}
        if len(expected) != len(plan.files):
            raise RuntimeError("Export plan contains case-insensitive duplicate paths")
        actual_files = 0
        for path in root.rglob("*"):
            if is_reparse(path):
                raise RuntimeError(f"Export tree contains a reparse path: {path}")
            if path.is_file():
                actual_files += 1
                relative = path.relative_to(root).as_posix()
                key = relative.casefold()
                if key not in expected:
                    raise RuntimeError(f"Export tree contains an unexpected file: {relative}")
        if actual_files != len(plan.files):
            missing = [
                file.destination_relative
                for file in plan.files
                if not self._destination(root, file.destination_relative).is_file()
            ][:5]
            if missing:
                raise RuntimeError(f"Export tree is missing files: {missing}")
            raise RuntimeError(
                f"Export tree file count differs: expected {len(plan.files)}, got {actual_files}"
            )

    def verify_tree(self, root: Path, plan: ExportPlan) -> None:
        self.verify_tree_layout(root, plan)
        for file in plan.files:
            self.verify_file(root, file)

    def publish_tree(self, staging_root: Path, output_root: Path) -> None:
        if output_root.exists():
            if not output_root.is_dir() or _retry_windows_lock(lambda: any(output_root.iterdir())):
                raise FileExistsError(f"Export output is not empty: {output_root}")
            _retry_windows_lock(output_root.rmdir)
        self._publish_part(staging_root, output_root)

    def publish_bytes(
        self,
        destination: Path,
        content: bytes,
        *,
        temporary_label: str,
    ) -> None:
        part = destination.with_name(f"{destination.name}.part")
        failure: BaseException | None = None
        try:
            self._write_content_file(part, content)
            self._publish_part(part, destination)
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                _retry_windows_lock(lambda: part.unlink(missing_ok=True))
            except BaseException as cleanup_error:
                if failure is None:
                    raise
                failure.add_note(
                    f"Unable to remove temporary {temporary_label} file {part}: {cleanup_error}"
                )

    @staticmethod
    def _destination(root: Path, relative: str) -> Path:
        path = root.joinpath(*Path(relative).parts).resolve(strict=False)
        path.relative_to(root.resolve(strict=True))
        return path

    @staticmethod
    def _copy_source_file(source: Path, destination: Path) -> None:
        def copy() -> None:
            # Keep the destination handle open while flushing it. Reopening it
            # immediately after copy2 is racy with Windows indexers and sync tools.
            with source.open("rb") as source_stream, destination.open("wb") as destination_stream:
                shutil.copyfileobj(source_stream, destination_stream)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
            shutil.copystat(source, destination)

        _retry_windows_lock(copy)

    @staticmethod
    def _write_content_file(destination: Path, content: bytes) -> None:
        def write() -> None:
            with destination.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())

        _retry_windows_lock(write)

    @staticmethod
    def _publish_part(part: Path, destination: Path) -> None:
        _retry_windows_lock(lambda: os.replace(part, destination))

    @staticmethod
    def _verify_path(path: Path, file: PlannedFile) -> None:
        if is_reparse(path) or not path.is_file():
            raise RuntimeError(f"Export file is missing or unsafe: {file.destination_relative}")
        if path.stat().st_size != file.size_bytes or sha256_file(path) != file.sha256:
            raise RuntimeError(f"Export file verification failed: {file.destination_relative}")
