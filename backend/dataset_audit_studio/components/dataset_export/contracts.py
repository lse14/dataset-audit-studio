from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dataset_audit_studio.core.dataset_artifacts import LatentRecordReference


@dataclass(frozen=True)
class PlannedFile:
    destination_relative: str
    sha256: str
    size_bytes: int
    kind: str
    source_path: Path | None = None
    content: bytes | None = None
    transcode_format: str | None = None


@dataclass(frozen=True)
class DatasetSummary:
    stage: int
    resolution: int
    relative_root: str
    file_count: int
    byte_count: int


@dataclass(frozen=True)
class AestheticEvidenceIdentity:
    source: str
    model_id: str
    config_hash: str
    algorithm_version: str


@dataclass(frozen=True)
class AestheticEvidence:
    sample_id: str
    value: Any
    source: str
    model_id: str | None
    config_hash: str | None
    algorithm_version: str


@dataclass(frozen=True)
class AestheticBinAssignment:
    sample_id: str
    directory: str
    reason: str | None
    value: float | None


@dataclass(frozen=True)
class AestheticBinPlan:
    assignments: tuple[AestheticBinAssignment, ...]
    bucket_counts: dict[str, int]
    unscored_reasons: dict[str, int]


@dataclass(frozen=True)
class ExportPlan:
    files: tuple[PlannedFile, ...]
    datasets: tuple[DatasetSummary, ...]
    latent_records: tuple[LatentRecordReference, ...]
    input_digest: str
    aesthetic_bin_plan: AestheticBinPlan | None = None


@dataclass(frozen=True)
class ExportSummary:
    task_id: str
    datasets: int
    files: int
    bytes: int
    resumed_from_file: int
    component_complete: bool
    artifact_cache_key: str | None
    final_status: str
    aesthetic_bucket_counts: dict[str, int] = field(default_factory=dict)
    aesthetic_unscored_reasons: dict[str, int] = field(default_factory=dict)
