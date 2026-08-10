from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path, PurePosixPath

from dataset_audit_studio.components.dataset_export.annotations import plan_paired_annotations
from dataset_audit_studio.components.dataset_export.contracts import (
    AestheticBinAssignment,
    AestheticBinPlan,
    AestheticEvidence,
    AestheticEvidenceIdentity,
    DatasetSummary,
    ExportPlan,
    PlannedFile,
)
from dataset_audit_studio.core.dataset_artifacts import (
    DatasetSample,
    DatasetWorkspace,
    LatentReferenceArtifact,
)
from dataset_audit_studio.core.file_integrity import (
    file_identity_matches,
    sha256_file,
)

_UNSCORED_DIRECTORY = "_unscored"
_UNSCORED_REASONS = (
    "missing",
    "non_finite",
    "out_of_range",
    "provenance_mismatch",
    "ambiguous",
)


def _matches_aesthetic_identity(
    evidence: AestheticEvidence,
    identity: AestheticEvidenceIdentity,
) -> bool:
    return (
        evidence.source == identity.source
        and evidence.model_id == identity.model_id
        and evidence.config_hash == identity.config_hash
        and evidence.algorithm_version == identity.algorithm_version
    )


def _numeric_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_aesthetic_bin_plan(
    *,
    sample_ids: tuple[str, ...],
    evidence: tuple[AestheticEvidence, ...],
    identity: AestheticEvidenceIdentity,
) -> AestheticBinPlan:
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("Aesthetic bin samples must be unique")
    requested = set(sample_ids)
    by_sample: dict[str, list[AestheticEvidence]] = defaultdict(list)
    for item in evidence:
        if item.sample_id in requested:
            by_sample[item.sample_id].append(item)

    assignments: list[AestheticBinAssignment] = []
    bucket_counts: dict[str, int] = defaultdict(int)
    reason_counts = {reason: 0 for reason in _UNSCORED_REASONS}
    for sample_id in sample_ids:
        records = by_sample.get(sample_id, [])
        current = [
            item for item in records if _matches_aesthetic_identity(item, identity)
        ]
        reason: str | None = None
        score: float | None = None
        if not current:
            reason = "missing" if not records else "provenance_mismatch"
        else:
            values = [_numeric_score(item.value) for item in current]
            if any(value is None for value in values):
                reason = "missing"
            else:
                numeric_values = [float(value) for value in values if value is not None]
                if any(not math.isfinite(value) for value in numeric_values):
                    reason = "non_finite"
                elif any(value < 1.0 or value > 5.0 for value in numeric_values):
                    reason = "out_of_range"
                else:
                    unique_scores = set(numeric_values)
                    if len(unique_scores) != 1:
                        reason = "ambiguous"
                    else:
                        score = unique_scores.pop()

        if reason is not None:
            assignments.append(
                AestheticBinAssignment(
                    sample_id=sample_id,
                    directory=_UNSCORED_DIRECTORY,
                    reason=reason,
                    value=None,
                )
            )
            reason_counts[reason] += 1
            continue
        assert score is not None
        directory = str(math.floor(score * 2))
        assignments.append(
            AestheticBinAssignment(
                sample_id=sample_id,
                directory=directory,
                reason=None,
                value=score,
            )
        )
        bucket_counts[directory] += 1

    return AestheticBinPlan(
        assignments=tuple(assignments),
        bucket_counts={
            directory: bucket_counts[directory]
            for directory in sorted(bucket_counts, key=int)
        },
        unscored_reasons=reason_counts,
    )


def _safe_relative(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError(f"Unsafe export relative path: {value}")
    return path


def _source_file(source_root: Path, relative: str) -> Path:
    safe = _safe_relative(relative)
    path = source_root.joinpath(*safe.parts).resolve(strict=True)
    path.relative_to(source_root)
    return path


def _image_file(
    sample: DatasetSample,
    *,
    prefix: PurePosixPath,
    verified: dict[str, tuple[Path, PurePosixPath, str, int, str]],
) -> PlannedFile:
    cached = verified.get(sample.sample_id)
    if cached is None:
        if not file_identity_matches(
            sample.source_path,
            size_bytes=sample.source_size,
            mtime_ns=sample.source_mtime_ns,
            sha256=sample.source_sha256,
        ):
            raise RuntimeError(f"Source image changed after scanning: {sample.relative_path}")
        relative = _safe_relative(sample.relative_path)
        source = sample.image_path if sample.export_requires_render else sample.source_path
        if sample.export_requires_render:
            relative = relative.with_suffix(".png")
        if not source.is_file():
            raise RuntimeError(f"Export image is missing: {sample.relative_path}")
        digest = sha256_file(source)
        if not sample.export_requires_render and digest != sample.source_sha256:
            raise RuntimeError(f"Source image SHA-256 changed: {sample.relative_path}")
        kind = "rendered_image" if sample.export_requires_render else "source_image"
        cached = (source, relative, digest, source.stat().st_size, kind)
        verified[sample.sample_id] = cached
    source, relative, digest, size_bytes, kind = cached
    return PlannedFile(
        destination_relative=(prefix / relative).as_posix(),
        source_path=source,
        content=None,
        sha256=digest,
        size_bytes=size_bytes,
        kind=kind,
    )


def _input_digest(
    files: tuple[PlannedFile, ...],
    datasets: tuple[DatasetSummary, ...],
    aesthetic_bin_plan: AestheticBinPlan | None = None,
) -> str:
    digest = hashlib.sha256()
    if aesthetic_bin_plan is not None:
        digest.update(b'{"aesthetic_bins":')
        digest.update(
            json.dumps(
                {
                    "assignments": [
                        assignment.__dict__
                        for assignment in aesthetic_bin_plan.assignments
                    ],
                    "bucket_counts": aesthetic_bin_plan.bucket_counts,
                    "unscored_reasons": aesthetic_bin_plan.unscored_reasons,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        )
        digest.update(b',"datasets":[')
    else:
        digest.update(b'{"datasets":[')
    for index, summary in enumerate(datasets):
        if index:
            digest.update(b",")
        digest.update(
            json.dumps(summary.__dict__, sort_keys=True, separators=(",", ":")).encode()
        )
    digest.update(b'],"files":[')
    for index, file in enumerate(files):
        if index:
            digest.update(b",")
        digest.update(
            json.dumps(
                [file.destination_relative, file.sha256, file.size_bytes, file.kind],
                separators=(",", ":"),
            ).encode()
        )
    digest.update(b"]}")
    return digest.hexdigest()


def build_export_plan(
    workspace: DatasetWorkspace,
    *,
    source_root: Path,
    latents: LatentReferenceArtifact | None,
    keep_annotation_files: bool = True,
    keep_latent_files: bool = True,
    aesthetic_bin_plan: AestheticBinPlan | None = None,
) -> ExportPlan:
    source_root = source_root.resolve(strict=True)
    samples = {sample.sample_id: sample for sample in workspace.samples}
    latent_map = (
        {(dataset.stage, dataset.resolution): dataset for dataset in latents.datasets}
        if keep_latent_files and latents is not None
        else {}
    )
    latent_datasets = latents.datasets if keep_latent_files and latents is not None else ()
    if len(latent_map) != len(latent_datasets):
        raise RuntimeError("Latent artifact contains duplicate dataset identities")
    files: dict[str, PlannedFile] = {}
    latent_records = {}
    verified_images: dict[str, tuple[Path, PurePosixPath, str, int, str]] = {}
    bin_directories: dict[str, str] = {}
    if aesthetic_bin_plan is not None:
        bin_directories = {
            assignment.sample_id: assignment.directory
            for assignment in aesthetic_bin_plan.assignments
        }
        if len(bin_directories) != len(aesthetic_bin_plan.assignments):
            raise RuntimeError("Aesthetic bin plan contains duplicate sample assignments")
        if set(bin_directories) != set(samples):
            raise RuntimeError("Aesthetic bin plan does not match export samples")

    def add_file(file: PlannedFile) -> bool:
        key = file.destination_relative.casefold()
        previous = files.get(key)
        if previous is not None:
            if (
                previous.destination_relative != file.destination_relative
                or previous.sha256 != file.sha256
                or previous.size_bytes != file.size_bytes
            ):
                raise RuntimeError(f"Export files collide at {file.destination_relative}")
            return False
        files[key] = file
        return True

    dataset_summaries: list[DatasetSummary] = []
    for dataset in workspace.datasets:
        prefix = PurePosixPath(f"stage{dataset.stage}") / str(dataset.resolution)
        dataset_file_count = 0
        dataset_byte_count = 0
        ordered = sorted(
            (samples[sample_id] for sample_id in dataset.sample_ids),
            key=lambda sample: (sample.relative_path.casefold(), sample.relative_path),
        )
        for sample in ordered:
            image_prefix = prefix
            if aesthetic_bin_plan is not None:
                image_prefix = prefix / bin_directories[sample.sample_id]
            image = _image_file(sample, prefix=image_prefix, verified=verified_images)
            if add_file(image):
                dataset_file_count += 1
                dataset_byte_count += image.size_bytes
            if keep_annotation_files:
                for annotation in plan_paired_annotations(
                    image_source=sample.source_path,
                    source_root=source_root,
                    destination_image=PurePosixPath(image.destination_relative),
                ):
                    if add_file(annotation):
                        dataset_file_count += 1
                        dataset_byte_count += annotation.size_bytes
        latent_dataset = latent_map.get((dataset.stage, dataset.resolution))
        if latent_dataset is not None:
            for copy in latent_dataset.copies:
                source = _source_file(source_root, copy.source_relative)
                if source.stat().st_size != copy.size_bytes or sha256_file(source) != copy.sha256:
                    raise RuntimeError(
                        f"Latent reference changed after resolve: {copy.source_relative}"
                    )
                planned_copy = PlannedFile(
                    destination_relative=(
                        prefix / _safe_relative(copy.destination_relative)
                    ).as_posix(),
                    source_path=source,
                    content=None,
                    sha256=copy.sha256,
                    size_bytes=copy.size_bytes,
                    kind=copy.kind,
                )
                if add_file(planned_copy):
                    dataset_file_count += 1
                    dataset_byte_count += planned_copy.size_bytes
            for catalog in latent_dataset.catalogs:
                content = catalog.content.encode("utf-8")
                if hashlib.sha256(content).hexdigest() != catalog.sha256:
                    raise RuntimeError("Latent catalog content changed after resolve")
                planned_catalog = PlannedFile(
                    destination_relative=(
                        prefix / _safe_relative(catalog.destination_relative)
                    ).as_posix(),
                    source_path=None,
                    content=content,
                    sha256=catalog.sha256,
                    size_bytes=len(content),
                    kind="mikazuki_catalog",
                )
                if add_file(planned_catalog):
                    dataset_file_count += 1
                    dataset_byte_count += planned_catalog.size_bytes
            for record in latent_dataset.records:
                latent_records[
                    (record.cache_kind, record.source_path, record.entry_key)
                ] = record
        dataset_summaries.append(
            DatasetSummary(
                stage=dataset.stage,
                resolution=dataset.resolution,
                relative_root=prefix.as_posix(),
                file_count=dataset_file_count,
                byte_count=dataset_byte_count,
            )
        )

    ordered_files = tuple(sorted(files.values(), key=lambda item: item.destination_relative))
    input_digest = _input_digest(
        ordered_files,
        tuple(dataset_summaries),
        aesthetic_bin_plan,
    )
    return ExportPlan(
        files=ordered_files,
        datasets=tuple(dataset_summaries),
        latent_records=tuple(latent_records.values()),
        input_digest=input_digest,
        aesthetic_bin_plan=aesthetic_bin_plan,
    )
