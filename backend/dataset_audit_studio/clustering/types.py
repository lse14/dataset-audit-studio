from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import torch


@dataclass(frozen=True)
class EmbeddingSample:
    sample_id: str
    relative_path: str
    artist_scope: str
    source_path: Path
    image_path: Path
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    pixel_sha256: str


@dataclass(frozen=True)
class EmbeddingBatch:
    sample_ids: tuple[str, ...]
    embeddings: np.ndarray


@dataclass(frozen=True)
class ClusteringScope:
    scope_id: str
    sample_indices: tuple[int, ...]


@dataclass(frozen=True)
class EmbeddingShard:
    cache_key: str
    relative_path: str
    sample_ids: tuple[str, ...]
    pixel_hashes: tuple[str, ...]
    model_sha256: str
    preprocessing_version: str
    sha256: str
    size_bytes: int
    rows: int
    dimensions: int


@dataclass(frozen=True)
class ClusterPlanNode:
    cluster_key: str
    parent_key: str | None
    scope_kind: str
    scope_id: str
    level: int
    sample_indices: tuple[int, ...]
    centroid: np.ndarray
    representative_index: int
    is_leaf: bool


@dataclass(frozen=True)
class DuplicateGroup:
    kind: str
    group_key: str
    member_indices: tuple[int, ...]
    representative_index: int
    member_scores: tuple[float | None, ...] = ()


@dataclass(frozen=True)
class SAEAnalysis:
    state_dict: dict[str, torch.Tensor]
    activations: np.ndarray
    thresholds: np.ndarray
    top_indices: tuple[tuple[int, ...], ...]
    losses: tuple[float, ...]


@dataclass(frozen=True)
class SAEArtifact:
    cache_key: str
    relative_path: str
    sha256: str
    size_bytes: int
    sample_ids: tuple[str, ...]
    input_dimensions: int
    feature_count: int
    thresholds: tuple[float, ...]
    top_indices: tuple[tuple[int, ...], ...]
    losses: tuple[float, ...]


@dataclass(frozen=True)
class SelectionSample:
    sample_id: str
    relative_path: str
    artist_scope: str
    source_sha256: str
    phash: str | None
    colorhash: str | None
    latent_count: int
    aesthetic_score: float | None
    domain_pass: bool
    ai_excluded: bool
    style_core: bool | None
    style_strong_outlier: bool
    style_approved_keep: bool
    duplicate_pinned: bool
    high_risk_count: int
    medium_risk_count: int


@dataclass(frozen=True)
class ResolutionFit:
    eligible: bool
    crop_loss: float
    upscale_factor: float


@dataclass(frozen=True)
class ClusterLeaf:
    cluster_key: str
    scope_id: str
    sample_indices: tuple[int, ...]
    coverage_rank: int


@dataclass(frozen=True)
class StageDecision:
    sample_id: str
    resolution: int
    stage: int
    included: bool
    reason_code: str | None


@dataclass(frozen=True)
class SelectionEvidence:
    sample_id: str
    code: str
    value: str
    severity: str
    review_only: bool
    metadata: dict


class EmbeddingRuntime(Protocol):
    def embed(self, samples: tuple[EmbeddingSample, ...]) -> EmbeddingBatch: ...

    def close(self) -> None: ...
