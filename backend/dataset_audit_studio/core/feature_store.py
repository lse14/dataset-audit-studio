from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from dataset_audit_studio.core.feature_batch import FeatureBatch


@dataclass(frozen=True)
class FeatureShard:
    producer_id: str
    cache_key: str
    relative_path: str
    sample_ids: tuple[str, ...]
    pixel_hashes: tuple[str, ...]
    capabilities: tuple[str, ...]
    dimensions: tuple[tuple[str, int], ...]
    model_digest: str
    preprocessing_version: str
    sha256: str
    size_bytes: int


class FeatureStore(Protocol):
    def cache_key(
        self,
        *,
        sample_ids: tuple[str, ...],
        pixel_hashes: tuple[str, ...],
        capabilities: tuple[str, ...],
        model_digest: str,
        preprocessing_version: str,
    ) -> str: ...

    def write(
        self,
        *,
        task_id: str,
        producer_id: str,
        sample_ids: tuple[str, ...],
        pixel_hashes: tuple[str, ...],
        features: dict[str, np.ndarray],
        model_digest: str,
        preprocessing_version: str,
    ) -> FeatureShard: ...

    def try_inspect(
        self,
        *,
        task_id: str,
        producer_id: str,
        cache_key: str,
    ) -> FeatureShard | None: ...

    def load(self, shard: FeatureShard) -> FeatureBatch: ...

