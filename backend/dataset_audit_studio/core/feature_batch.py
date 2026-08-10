from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

import numpy as np


@dataclass(frozen=True)
class FeatureBatch:
    sample_ids: tuple[str, ...]
    features: MappingProxyType[str, np.ndarray]

    @classmethod
    def create(
        cls,
        sample_ids: tuple[str, ...],
        features: dict[str, np.ndarray],
    ) -> FeatureBatch:
        checked: dict[str, np.ndarray] = {}
        for capability, values in features.items():
            matrix = np.ascontiguousarray(values, dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape[0] != len(sample_ids):
                raise ValueError(f"Feature {capability} shape does not match sample ids")
            if np.any(~np.isfinite(matrix)):
                raise ValueError(f"Feature {capability} contains non-finite values")
            matrix.setflags(write=False)
            checked[capability] = matrix
        return cls(sample_ids=sample_ids, features=MappingProxyType(checked))

    def get(self, capability: str) -> np.ndarray:
        try:
            return self.features[capability]
        except KeyError as error:
            raise KeyError(f"Feature batch is missing capability {capability}") from error

