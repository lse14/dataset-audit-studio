from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


def is_reparse(path: Path) -> bool:
    result = path.lstat()
    attributes = getattr(result, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def require_regular_file(path: Path, root: Path) -> Path:
    resolved_root = root.resolve(strict=True)
    if is_reparse(path) or not path.is_file():
        raise RuntimeError(f"Latent cache file is missing or unsafe: {path.name}")
    resolved = path.resolve(strict=True)
    resolved.relative_to(resolved_root)
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_is_unchanged(sample) -> bool:
    if sample.export_requires_render:
        return False
    try:
        before = sample.source_path.stat()
        if (
            before.st_size != sample.source_size
            or before.st_mtime_ns != sample.source_mtime_ns
        ):
            return False
        digest = sha256_file(sample.source_path)
        after = sample.source_path.stat()
    except OSError:
        return False
    return (
        digest == sample.source_sha256
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )


def fsync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "fsync_directory",
    "is_reparse",
    "require_regular_file",
    "sha256_file",
    "source_is_unchanged",
]
