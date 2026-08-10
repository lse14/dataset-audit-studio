from __future__ import annotations

from dataclasses import dataclass

import numpy as np


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
