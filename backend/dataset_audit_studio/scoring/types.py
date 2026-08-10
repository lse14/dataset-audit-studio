from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from dataset_audit_studio.core.model_assets import AssetFile, ModelAsset, RuntimeAssets

__all__ = [
    "AssetFile",
    "ComponentIdentity",
    "EvidenceRecord",
    "ModelAsset",
    "RuntimeAssets",
    "SampleInput",
    "SampleScore",
    "ScoringRuntime",
]


@dataclass(frozen=True)
class ComponentIdentity:
    component: str
    model_id: str
    model_sha256: str
    preprocessing_version: str
    config_hash: str
    evidence_source: str


@dataclass(frozen=True)
class SampleInput:
    sample_id: str
    relative_path: str
    artist_scope: str
    source_path: Path
    image_path: Path
    source_size: int
    source_mtime_ns: int
    pixel_sha256: str


@dataclass(frozen=True)
class EvidenceRecord:
    code: str
    source: str
    value: Any
    threshold: Any | None
    severity: str
    review_only: bool
    bbox: list[float] | None
    algorithm_version: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SampleScore:
    sample_id: str
    results: dict[str, dict[str, Any]]


class ScoringRuntime(Protocol):
    def score_batch(self, samples: tuple[SampleInput, ...]) -> tuple[SampleScore, ...]: ...

    def close(self) -> None: ...
