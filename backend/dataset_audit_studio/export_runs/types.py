from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ExportRunView:
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


@dataclass(frozen=True)
class ExportRunPreview:
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
