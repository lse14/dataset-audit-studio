from __future__ import annotations

import hashlib
import stat
from pathlib import Path


def is_reparse(path: Path) -> bool:
    result = path.lstat()
    attributes = getattr(result, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity_matches(
    path: Path,
    *,
    size_bytes: int,
    mtime_ns: int,
    sha256: str,
) -> bool:
    try:
        before = path.stat()
        if before.st_size != size_bytes or before.st_mtime_ns != mtime_ns:
            return False
        digest = sha256_file(path)
        after = path.stat()
    except OSError:
        return False
    return (
        digest == sha256
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )
