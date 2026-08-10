from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from dataset_audit_studio.database.enums import ArtifactState
from dataset_audit_studio.database.models import (
    Artifact,
    ClusterMembership,
    Evidence,
    ModelResult,
    ResolutionAssessment,
    ReviewDecision,
    Sample,
)
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scanner.types import ManifestInfo, ScannedMedia

UNREADABLE_SHA256 = "0" * 64


def prepare_scan(session: Session, task_id: str) -> None:
    session.execute(update(Sample).where(Sample.task_id == task_id).values(scan_state="missing"))


def _moved_candidate(
    session: Session,
    *,
    task_id: str,
    source_sha256: str,
    active_paths: set[str],
) -> Sample | None:
    if source_sha256 == UNREADABLE_SHA256:
        return None
    candidates = session.scalars(
        select(Sample)
        .where(
            Sample.task_id == task_id,
            Sample.source_sha256 == source_sha256,
            Sample.scan_state == "missing",
        )
        .order_by(Sample.relative_path)
    ).all()
    return next(
        (candidate for candidate in candidates if candidate.relative_path not in active_paths),
        None,
    )


def _invalidate_pixel_dependent(session: Session, sample_id: str) -> None:
    for model in (ModelResult, Evidence, ClusterMembership, Artifact):
        session.execute(delete(model).where(model.sample_id == sample_id))
    session.execute(
        update(ReviewDecision)
        .where(ReviewDecision.sample_id == sample_id, ReviewDecision.is_active.is_(True))
        .values(is_active=False)
    )


def _replace_scanner_outputs(
    session: Session,
    sample: Sample,
    item: ScannedMedia,
    *,
    config_hash: str,
) -> None:
    session.execute(
        delete(Evidence).where(
            Evidence.sample_id == sample.id,
            or_(
                Evidence.source == "scanner",
                Evidence.source.like("technical_metrics_v%"),
            ),
        )
    )
    session.execute(
        delete(ResolutionAssessment).where(
            ResolutionAssessment.sample_id == sample.id,
            ResolutionAssessment.config_hash == config_hash,
        )
    )

    for metric in item.evidence:
        numeric = (
            float(metric.value)
            if isinstance(metric.value, (int, float)) and not isinstance(metric.value, bool)
            else None
        )
        threshold_numeric = (
            float(metric.threshold)
            if isinstance(metric.threshold, (int, float)) and not isinstance(metric.threshold, bool)
            else None
        )
        algorithm_version = str(metric.metadata.get("algorithm_version", metric.source))
        session.add(
            Evidence(
                task_id=sample.task_id,
                sample_id=sample.id,
                code=metric.code,
                source=metric.source,
                value_json=metric.value,
                threshold_json=metric.threshold,
                value_number=numeric,
                threshold_number=threshold_numeric,
                metadata_json=dict(metric.metadata),
                severity=metric.severity,
                review_only=metric.review_only,
                bbox_json=None,
                algorithm_version=algorithm_version,
            )
        )

    for assessment in item.resolutions:
        session.add(
            ResolutionAssessment(
                task_id=sample.task_id,
                sample_id=sample.id,
                resolution=assessment.resolution,
                config_hash=config_hash,
                area_pixels=assessment.area_pixels,
                minimum_area=assessment.minimum_area,
                area_pass=assessment.area_pass,
                bucket_width=assessment.bucket_width,
                bucket_height=assessment.bucket_height,
                upscale_factor=assessment.upscale_factor,
                crop_loss=assessment.crop_loss,
                aspect_ratio=assessment.aspect_ratio,
                eligible=assessment.eligible,
                risk_codes=list(assessment.risk_codes),
            )
        )


def upsert_scanned_batch(
    session: Session,
    *,
    task_id: str,
    config_hash: str,
    active_paths: set[str],
    items: tuple[ScannedMedia, ...],
    algorithm_version: str,
) -> None:
    for item in items:
        sample = session.scalar(
            select(Sample).where(
                Sample.task_id == task_id, Sample.relative_path == item.relative_path
            )
        )
        if sample is None:
            sample = _moved_candidate(
                session,
                task_id=task_id,
                source_sha256=item.source_sha256,
                active_paths=active_paths,
            )
        is_new = sample is None
        if sample is None:
            sample = Sample(
                task_id=task_id,
                relative_path=item.relative_path,
                source_size=item.source_size,
                source_mtime_ns=item.source_mtime_ns,
                source_sha256=item.source_sha256,
                media_kind=item.media_kind,
                artist_scope=item.artist_scope,
                scan_state=item.scan_state,
            )
            session.add(sample)
            session.flush()

        verified_source = item.source_sha256 != UNREADABLE_SHA256
        pixel_changed = not is_new and verified_source and sample.pixel_sha256 != item.pixel_sha256
        if pixel_changed:
            _invalidate_pixel_dependent(session, sample.id)
        sample.relative_path = item.relative_path
        sample.source_size = item.source_size
        sample.source_mtime_ns = item.source_mtime_ns
        if verified_source or is_new:
            sample.source_sha256 = item.source_sha256
            sample.pixel_sha256 = item.pixel_sha256
            sample.encoded_width = item.encoded_width
            sample.encoded_height = item.encoded_height
            sample.display_width = item.display_width
            sample.display_height = item.display_height
            sample.frame_count = item.frame_count
            sample.is_animated = item.is_animated
            sample.exif_orientation = item.exif_orientation
            sample.extracted_frame_path = item.extracted_frame_path
            sample.export_requires_render = item.export_requires_render
            sample.phash = item.phash
            sample.colorhash = item.colorhash
        sample.media_kind = item.media_kind
        sample.artist_scope = item.artist_scope
        sample.scan_state = item.scan_state
        sample.scan_algorithm_version = algorithm_version

        _replace_scanner_outputs(session, sample, item, config_hash=config_hash)


def upsert_manifest_artifact(
    session: Session,
    *,
    task_id: str,
    config_hash: str,
    manifest: ManifestInfo,
    project_root: Path | None = None,
) -> Artifact:
    cache_key = f"{config_hash}:{manifest.sha256}"
    artifact = session.scalar(
        select(Artifact).where(
            Artifact.task_id == task_id,
            Artifact.kind == "scan_manifest",
            Artifact.cache_key == cache_key,
        )
    )
    project_root = (project_root or PROJECT_ROOT).resolve(strict=False)
    relative_path = manifest.path.relative_to(project_root).as_posix()
    metadata = {
        "item_count": manifest.item_count,
        "ignored_reparse_count": manifest.ignored_reparse_count,
        "ignored_directory_count": manifest.ignored_directory_count,
    }
    if artifact is None:
        artifact = Artifact(
            task_id=task_id,
            sample_id=None,
            kind="scan_manifest",
            phase="scanning",
            cache_key=cache_key,
            path=relative_path,
            sha256=manifest.sha256,
            size_bytes=manifest.path.stat().st_size,
            state=ArtifactState.READY.value,
            metadata_json=metadata,
        )
        session.add(artifact)
    else:
        artifact.path = relative_path
        artifact.sha256 = manifest.sha256
        artifact.size_bytes = manifest.path.stat().st_size
        artifact.state = ArtifactState.READY.value
        artifact.metadata_json = metadata
    session.flush()
    return artifact


def project_cache_path(
    relative_path: str | None,
    *,
    project_root: Path | None = None,
) -> Path | None:
    if relative_path is None:
        return None
    project_root = (project_root or PROJECT_ROOT).resolve(strict=False)
    path = project_root.joinpath(*Path(relative_path).parts).resolve(strict=False)
    path.relative_to(project_root)
    return path
