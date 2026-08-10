from __future__ import annotations

from pathlib import Path, PurePosixPath

from dataset_audit_studio.components.dataset_export.contracts import PlannedFile
from dataset_audit_studio.core.file_integrity import is_reparse, sha256_file

ANNOTATION_SUFFIXES = (".txt", ".json")


def plan_paired_annotations(
    *,
    image_source: Path,
    source_root: Path,
    destination_image: PurePosixPath,
) -> tuple[PlannedFile, ...]:
    resolved_root = source_root.resolve(strict=True)
    files: list[PlannedFile] = []
    for suffix in ANNOTATION_SUFFIXES:
        candidate = image_source.with_suffix(suffix)
        if not candidate.exists():
            continue
        if is_reparse(candidate):
            raise RuntimeError(f"Annotation file is unsafe: {candidate}")
        source = candidate.resolve(strict=True)
        source.relative_to(resolved_root)
        if not source.is_file():
            raise RuntimeError(f"Annotation file is not regular: {source}")
        files.append(
            PlannedFile(
                destination_relative=destination_image.with_suffix(suffix).as_posix(),
                source_path=source,
                sha256=sha256_file(source),
                size_bytes=source.stat().st_size,
                kind="source_annotation",
            )
        )
    return tuple(files)
