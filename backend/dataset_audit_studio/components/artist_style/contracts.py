from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class StyleSample:
    sample_id: str
    relative_path: str
    artist_scope: str
    source_path: Path
    image_path: Path
    source_size: int
    source_mtime_ns: int
    pixel_sha256: str


@dataclass(frozen=True)
class StyleScope:
    scope_id: str
    samples: tuple[StyleSample, ...]


@dataclass(frozen=True)
class StyleFeatureBatch:
    sample_ids: tuple[str, ...]
    lsnet: np.ndarray
    gram: np.ndarray
    dino: np.ndarray
    color_histogram: np.ndarray


@dataclass(frozen=True)
class StyleAssessment:
    sample_id: str
    style_score: float
    lsnet_average_similarity: float
    lsnet_average_score: float
    gram_average_similarity: float
    gram_average_score: float
    gram_centroid_distance: float
    gram_centroid_score: float
    dino_average_similarity: float
    dino_guardrail_score: float
    color_histogram_l1: float
    core_member: bool
    strong_outlier: bool
    review_required: bool
    outlier_reason: str | None
    iteration_removed: int | None
    lsnet_threshold: float
    gram_threshold: float
    dino_threshold: float


class StyleFeatureRuntime(Protocol):
    def extract(self, samples: tuple[StyleSample, ...]) -> StyleFeatureBatch: ...

    def close(self) -> None: ...
