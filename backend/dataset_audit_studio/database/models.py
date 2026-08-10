from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from dataset_audit_studio.database.base import Base, TimestampMixin, utc_now
from dataset_audit_studio.database.enums import (
    ArtifactState,
    ComponentRunState,
    ExportRunStatus,
    TaskStatus,
)


def new_id() -> str:
    return str(uuid4())


def _allowed(
    values: type[TaskStatus]
    | type[ArtifactState]
    | type[ComponentRunState]
    | type[ExportRunStatus],
) -> str:
    return ", ".join(f"'{value.value}'" for value in values)


TASK_STATUS_SQL = _allowed(TaskStatus)
ARTIFACT_STATE_SQL = _allowed(ArtifactState)
EXPORT_RUN_STATUS_SQL = _allowed(ExportRunStatus)
COMPONENT_RUN_STATE_SQL = _allowed(ComponentRunState)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(f"status IN ({TASK_STATUS_SQL})", name="status"),
        CheckConstraint(
            f"resume_state IS NULL OR resume_state IN ({TASK_STATUS_SQL})", name="resume_state"
        ),
        CheckConstraint("progress_current >= 0", name="progress_current_nonnegative"),
        CheckConstraint(
            "progress_total IS NULL OR progress_total >= progress_current",
            name="progress_total_valid",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint("execution_epoch >= 0", name="execution_epoch_nonnegative"),
        Index("ix_tasks_queue", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    source_root: Mapped[str] = mapped_column(Text)
    output_root: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.DRAFT.value, index=True)
    resume_state: Mapped[str | None] = mapped_column(String(32))
    current_config_revision: Mapped[int] = mapped_column(Integer, default=1)
    progress_current: Mapped[int] = mapped_column(BigInteger, default=0)
    progress_total: Mapped[int | None] = mapped_column(BigInteger)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    execution_epoch: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskConfig(Base):
    __tablename__ = "task_configs"
    __table_args__ = (
        UniqueConstraint("task_id", "revision", name="task_revision"),
        UniqueConstraint("task_id", "config_hash", name="task_hash"),
        CheckConstraint("revision >= 1", name="revision_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    config_hash: Mapped[str] = mapped_column(String(64))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TaskPreset(TimestampMixin, Base):
    __tablename__ = "task_presets"
    __table_args__ = (
        UniqueConstraint("name_key", name="task_preset_name_key"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(400))
    components_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class WorkerLease(Base):
    __tablename__ = "worker_leases"
    __table_args__ = (
        CheckConstraint("slot_id = 1", name="single_slot"),
        CheckConstraint("execution_epoch >= 1", name="execution_epoch_positive"),
        CheckConstraint(
            "(task_id IS NOT NULL AND export_run_id IS NULL) OR "
            "(task_id IS NULL AND export_run_id IS NOT NULL)",
            name="exactly_one_target",
        ),
    )

    slot_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), unique=True
    )
    export_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("export_runs.id", ondelete="CASCADE"), unique=True
    )
    owner: Mapped[str] = mapped_column(String(160))
    execution_epoch: Mapped[int] = mapped_column(Integer)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PhaseCheckpoint(Base):
    __tablename__ = "phase_checkpoints"
    __table_args__ = (
        UniqueConstraint("task_id", "phase", "config_hash", "batch_index", name="batch_identity"),
        CheckConstraint("batch_index >= 0", name="batch_index_nonnegative"),
        CheckConstraint("completed_items >= 0", name="completed_items_nonnegative"),
        CheckConstraint("execution_epoch >= 1", name="execution_epoch_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    batch_index: Mapped[int] = mapped_column(Integer)
    cursor_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    completed_items: Mapped[int] = mapped_column(BigInteger)
    execution_epoch: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ComponentRun(TimestampMixin, Base):
    __tablename__ = "component_runs"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "component_id",
            "config_hash",
            name="task_component_config",
        ),
        CheckConstraint(
            f"status IN ({COMPONENT_RUN_STATE_SQL})",
            name="status",
        ),
        CheckConstraint("phase_order >= 0", name="phase_order_nonnegative"),
        CheckConstraint("completed_items >= 0", name="completed_items_nonnegative"),
        CheckConstraint(
            "total_items IS NULL OR total_items >= completed_items",
            name="total_items_valid",
        ),
        Index("ix_component_runs_task_status", "task_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
    )
    component_id: Mapped[str] = mapped_column(String(160))
    component_version: Mapped[str] = mapped_column(String(32))
    phase: Mapped[str] = mapped_column(String(64), index=True)
    phase_order: Mapped[int] = mapped_column(Integer)
    execution: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(24),
        default=ComponentRunState.PENDING.value,
    )
    config_hash: Mapped[str] = mapped_column(String(64))
    config_digest: Mapped[str] = mapped_column(String(64))
    input_digest: Mapped[str | None] = mapped_column(String(64))
    model_digest: Mapped[str | None] = mapped_column(String(64))
    normalized_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dependency_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    model_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    completed_items: Mapped[int] = mapped_column(BigInteger, default=0)
    total_items: Mapped[int | None] = mapped_column(BigInteger)
    auto_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Sample(TimestampMixin, Base):
    __tablename__ = "samples"
    __table_args__ = (
        UniqueConstraint("task_id", "relative_path", name="task_relative_path"),
        CheckConstraint("source_size >= 0", name="source_size_nonnegative"),
        CheckConstraint(
            "encoded_width IS NULL OR encoded_width > 0", name="encoded_width_positive"
        ),
        CheckConstraint(
            "encoded_height IS NULL OR encoded_height > 0", name="encoded_height_positive"
        ),
        CheckConstraint(
            "display_width IS NULL OR display_width > 0", name="display_width_positive"
        ),
        CheckConstraint(
            "display_height IS NULL OR display_height > 0", name="display_height_positive"
        ),
        CheckConstraint("frame_count IS NULL OR frame_count >= 1", name="frame_count_positive"),
        CheckConstraint(
            "exif_orientation IS NULL OR exif_orientation BETWEEN 1 AND 8",
            name="exif_orientation_valid",
        ),
        Index("ix_samples_source_sha", "task_id", "source_sha256"),
        Index("ix_samples_scan_state", "task_id", "scan_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    relative_path: Mapped[str] = mapped_column(Text)
    source_size: Mapped[int] = mapped_column(BigInteger)
    source_mtime_ns: Mapped[int] = mapped_column(BigInteger)
    source_sha256: Mapped[str] = mapped_column(String(64))
    pixel_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    media_kind: Mapped[str] = mapped_column(String(32))
    artist_scope: Mapped[str] = mapped_column(Text)
    scan_state: Mapped[str] = mapped_column(String(32), default="discovered")
    encoded_width: Mapped[int | None] = mapped_column(Integer)
    encoded_height: Mapped[int | None] = mapped_column(Integer)
    display_width: Mapped[int | None] = mapped_column(Integer)
    display_height: Mapped[int | None] = mapped_column(Integer)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    is_animated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    exif_orientation: Mapped[int | None] = mapped_column(Integer)
    extracted_frame_path: Mapped[str | None] = mapped_column(Text)
    export_requires_render: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    phash: Mapped[str | None] = mapped_column(String(64), index=True)
    colorhash: Mapped[str | None] = mapped_column(String(64), index=True)
    scan_algorithm_version: Mapped[str | None] = mapped_column(String(80))


class ModelResult(Base):
    __tablename__ = "model_results"
    __table_args__ = (
        UniqueConstraint(
            "sample_id",
            "model_id",
            "model_sha256",
            "preprocessing_version",
            "config_hash",
            name="cache_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    sample_id: Mapped[str] = mapped_column(ForeignKey("samples.id", ondelete="CASCADE"), index=True)
    model_id: Mapped[str] = mapped_column(String(160))
    model_sha256: Mapped[str] = mapped_column(String(64))
    preprocessing_version: Mapped[str] = mapped_column(String(80))
    config_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_task_code", "task_id", "code"),
        Index("ix_evidence_code_value", "task_id", "code", "value_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    sample_id: Mapped[str] = mapped_column(ForeignKey("samples.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(120))
    value_json: Mapped[Any] = mapped_column(JSON)
    threshold_json: Mapped[Any | None] = mapped_column(JSON)
    value_number: Mapped[float | None] = mapped_column(Float)
    threshold_number: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    severity: Mapped[str] = mapped_column(String(24))
    review_only: Mapped[bool] = mapped_column(Boolean, default=False)
    bbox_json: Mapped[list[float] | None] = mapped_column(JSON)
    algorithm_version: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (Index("ix_review_active", "task_id", "category", "is_active"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    sample_id: Mapped[str | None] = mapped_column(
        ForeignKey("samples.id", ondelete="CASCADE"), index=True
    )
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32), default="human")
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_decisions.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClusterNode(Base):
    __tablename__ = "cluster_nodes"
    __table_args__ = (
        UniqueConstraint("task_id", "cluster_key", name="task_cluster_key"),
        CheckConstraint("level >= 0", name="level_nonnegative"),
        CheckConstraint("size >= 0", name="size_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("cluster_nodes.id", ondelete="CASCADE"), index=True
    )
    cluster_key: Mapped[str] = mapped_column(String(160))
    scope_kind: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str] = mapped_column(Text)
    level: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    centroid_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClusterMembership(Base):
    __tablename__ = "cluster_memberships"

    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("cluster_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    sample_id: Mapped[str] = mapped_column(
        ForeignKey("samples.id", ondelete="CASCADE"), primary_key=True
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    score: Mapped[float | None] = mapped_column(Float)
    is_representative: Mapped[bool] = mapped_column(Boolean, default=False)


class ResolutionAssessment(Base):
    __tablename__ = "resolution_assessments"
    __table_args__ = (
        UniqueConstraint("sample_id", "resolution", "config_hash", name="sample_resolution_config"),
        CheckConstraint("resolution > 0", name="resolution_positive"),
        CheckConstraint("area_pixels >= 0", name="area_nonnegative"),
        CheckConstraint("minimum_area > 0", name="minimum_area_positive"),
        CheckConstraint("bucket_width > 0", name="bucket_width_positive"),
        CheckConstraint("bucket_height > 0", name="bucket_height_positive"),
        CheckConstraint("upscale_factor >= 0", name="upscale_nonnegative"),
        CheckConstraint("crop_loss BETWEEN 0 AND 1", name="crop_loss_valid"),
        CheckConstraint("aspect_ratio > 0", name="aspect_ratio_positive"),
        Index("ix_resolution_task_resolution", "task_id", "resolution", "eligible"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    sample_id: Mapped[str] = mapped_column(ForeignKey("samples.id", ondelete="CASCADE"), index=True)
    resolution: Mapped[int] = mapped_column(Integer)
    config_hash: Mapped[str] = mapped_column(String(64))
    area_pixels: Mapped[int] = mapped_column(BigInteger)
    minimum_area: Mapped[int] = mapped_column(BigInteger)
    area_pass: Mapped[bool] = mapped_column(Boolean)
    bucket_width: Mapped[int] = mapped_column(Integer)
    bucket_height: Mapped[int] = mapped_column(Integer)
    upscale_factor: Mapped[float] = mapped_column(Float)
    crop_loss: Mapped[float] = mapped_column(Float)
    aspect_ratio: Mapped[float] = mapped_column(Float)
    eligible: Mapped[bool] = mapped_column(Boolean)
    risk_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Artifact(TimestampMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("task_id", "kind", "cache_key", name="cache_identity"),
        CheckConstraint(f"state IN ({ARTIFACT_STATE_SQL})", name="state"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    sample_id: Mapped[str | None] = mapped_column(
        ForeignKey("samples.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(80))
    phase: Mapped[str] = mapped_column(String(64))
    cache_key: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16), default=ArtifactState.WRITING.value)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ExportRun(TimestampMixin, Base):
    __tablename__ = "export_runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({EXPORT_RUN_STATUS_SQL})", name="status"),
        CheckConstraint("task_config_revision >= 1", name="config_revision_positive"),
        CheckConstraint("selection_version >= 1", name="selection_version_positive"),
        CheckConstraint("minimum_resolution > 0", name="minimum_resolution_positive"),
        CheckConstraint(
            "aesthetic_minimum IS NULL OR (aesthetic_minimum >= 1.0 AND aesthetic_minimum <= 5.0)",
            name="aesthetic_minimum_valid",
        ),
        CheckConstraint("minimum_folder_images > 0", name="minimum_folder_images_positive"),
        CheckConstraint(
            "sample_seen_mode IN ('off', 'auto', 'manual')", name="sample_seen_mode_valid"
        ),
        CheckConstraint(
            "(sample_seen_mode = 'manual' AND sample_seen_target > 0) OR "
            "(sample_seen_mode IN ('off', 'auto') AND sample_seen_target IS NULL)",
            name="sample_seen_target_valid",
        ),
        CheckConstraint("execution_epoch >= 0", name="execution_epoch_nonnegative"),
        CheckConstraint("progress_current >= 0", name="progress_current_nonnegative"),
        CheckConstraint(
            "progress_total IS NULL OR progress_total >= progress_current",
            name="progress_total_valid",
        ),
        CheckConstraint("bytes_current >= 0", name="bytes_current_nonnegative"),
        CheckConstraint(
            "bytes_total IS NULL OR bytes_total >= bytes_current",
            name="bytes_total_valid",
        ),
        CheckConstraint("file_count >= 0", name="file_count_nonnegative"),
        Index("ix_export_runs_task_created", "task_id", "created_at", "id"),
        Index("ix_export_runs_queue", "status", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_config_revision: Mapped[int] = mapped_column(Integer)
    config_hash: Mapped[str] = mapped_column(String(64))
    selection_version: Mapped[int] = mapped_column(Integer)
    output_root: Mapped[str] = mapped_column(Text)
    output_key: Mapped[str] = mapped_column(String(2048), unique=True)
    minimum_resolution: Mapped[int] = mapped_column(Integer)
    resolutions_json: Mapped[list[int]] = mapped_column(JSON, default=list)
    aesthetic_minimum: Mapped[float | None] = mapped_column(Float)
    minimum_folder_images: Mapped[int] = mapped_column(Integer, default=1)
    add_repeat_prefix: Mapped[bool] = mapped_column(Boolean, default=True)
    sample_seen_mode: Mapped[str] = mapped_column(String(8), default="off")
    sample_seen_target: Mapped[int | None] = mapped_column(Integer)
    preview_digest: Mapped[str | None] = mapped_column(String(64))
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    aesthetic_identity_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(24), default=ExportRunStatus.QUEUED.value, index=True
    )
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_digest: Mapped[str | None] = mapped_column(String(64))
    input_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    execution_epoch: Mapped[int] = mapped_column(Integer, default=0)
    progress_current: Mapped[int] = mapped_column(BigInteger, default=0)
    progress_total: Mapped[int | None] = mapped_column(BigInteger)
    bytes_current: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger)
    file_count: Mapped[int] = mapped_column(BigInteger, default=0)
    manifest_path: Mapped[str | None] = mapped_column(Text)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="task_sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        Index("ix_task_events_task_created", "task_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
