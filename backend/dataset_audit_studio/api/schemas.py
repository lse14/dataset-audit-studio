from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dataset_audit_studio.core.profile_contracts import DatasetProfile
from dataset_audit_studio.export_runs.types import ExportRunPreview, ExportRunView
from dataset_audit_studio.jobs.types import CheckpointView, TaskEventView, TaskView


class ComponentConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    config: dict[str, Any]


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_root: str = Field(min_length=1)
    output_root: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    profile: DatasetProfile | None = None
    components: dict[str, ComponentConfigInput] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Task name must not be blank")
        return cleaned


class TaskConfigUpdate(BaseModel):
    config: dict[str, Any]
    expected_version: int | None = Field(default=None, ge=1)


class TaskControlRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)


class RewritePreviewConfirmationRequest(TaskControlRequest):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskTerminateRequest(TaskControlRequest):
    force: bool = False
    reason: str | None = Field(default=None, max_length=500)


class TaskDeleteRequest(TaskControlRequest):
    pass


class TaskDeleteResponse(BaseModel):
    task_id: str
    cache_cleared: bool
    cache_cleanup_error: str | None = None


class ReviewGateReleaseRequest(TaskControlRequest):
    model_config = ConfigDict(extra="forbid")

    expected_gate: str
    output_root: Any = None
    minimum_resolution: Any = None
    domain_minimum: Any = None
    exclude_exact_visual_duplicates: Any = None
    style_outlier_mode: Any = None
    aesthetic_minimum: Any = None
    minimum_folder_images: Any = None
    add_repeat_prefix: Any = None
    sample_seen_mode: Any = None
    sample_seen_target: Any = None
    preview_digest: Any = None


class WatermarkReviewThresholdRequest(TaskControlRequest):
    threshold: float = Field(ge=0.0, le=1.0)


class WatermarkReviewThresholdResponse(BaseModel):
    threshold: float
    updated: int
    candidates: int


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    source_root: str
    output_root: str | None
    status: str
    resume_state: str | None
    current_config_revision: int
    config_hash: str
    config: dict[str, Any]
    progress_current: int
    progress_total: int | None
    row_version: int
    execution_epoch: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_view(cls, task: TaskView) -> TaskResponse:
        return cls.model_validate(task)


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    offset: int
    limit: int


class ExportRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_root: Any = None
    minimum_resolution: Any = None
    domain_minimum: Any = None
    exclude_exact_visual_duplicates: Any = None
    style_outlier_mode: Any = None
    aesthetic_minimum: Any = None
    minimum_folder_images: Any = None
    add_repeat_prefix: Any = None
    sample_seen_mode: Any = None
    sample_seen_target: Any = None
    preview_digest: Any = None


class ExportRunPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_root: Any = None
    minimum_resolution: Any = None
    domain_minimum: Any = None
    exclude_exact_visual_duplicates: Any = None
    style_outlier_mode: Any = None
    aesthetic_minimum: Any = None
    minimum_folder_images: Any = None
    add_repeat_prefix: Any = None
    sample_seen_mode: Any = None
    sample_seen_target: Any = None


class ExportRunPreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    minimum_resolution: int
    domain_minimum: float | None
    exclude_exact_visual_duplicates: bool
    style_outlier_mode: str
    aesthetic_minimum: float | None
    minimum_folder_images: int
    add_repeat_prefix: bool
    sample_seen_mode: str
    sample_seen_target: int | None
    preview_digest: str
    input_digest: str
    eligibility_digest: str
    settings: dict[str, Any]
    included_count: int
    exclusion_counts: dict[str, int]
    folder_below_minimum: dict[str, int]
    folders: tuple[dict[str, Any], ...]
    duplicate_groups: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    @classmethod
    def from_preview(cls, preview: ExportRunPreview) -> ExportRunPreviewResponse:
        return cls.model_validate(preview)


class ExportRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    task_config_revision: int
    config_hash: str
    selection_version: int
    output_root: str
    output_key: str
    minimum_resolution: int
    resolutions: tuple[int, ...]
    aesthetic_minimum: float | None
    minimum_folder_images: int
    add_repeat_prefix: bool
    sample_seen_mode: str
    sample_seen_target: int | None
    preview_digest: str | None
    settings: dict[str, Any]
    aesthetic_identity: dict[str, Any] | None
    status: str
    checkpoint: dict[str, Any]
    input_digest: str | None
    execution_epoch: int
    progress_current: int
    progress_total: int | None
    bytes_current: int
    bytes_total: int | None
    file_count: int
    manifest_path: str | None
    manifest_sha256: str | None
    summary: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_view(cls, run: ExportRunView) -> ExportRunResponse:
        return cls.model_validate(run)


class ExportRunListResponse(BaseModel):
    items: list[ExportRunResponse]
    total: int
    offset: int
    limit: int


class TaskEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    event_type: str
    from_status: str | None
    to_status: str | None
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_view(cls, event: TaskEventView) -> TaskEventResponse:
        return cls.model_validate(event)


class TaskEventListResponse(BaseModel):
    items: list[TaskEventResponse]
    next_after: int
    latest_sequence: int


class CheckpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: str
    phase: str
    config_hash: str
    batch_index: int
    cursor: dict[str, Any]
    completed_items: int
    execution_epoch: int
    artifact_id: str | None
    created_at: datetime

    @classmethod
    def from_view(cls, checkpoint: CheckpointView) -> CheckpointResponse:
        return cls.model_validate(checkpoint)


class CheckpointListResponse(BaseModel):
    items: list[CheckpointResponse]
