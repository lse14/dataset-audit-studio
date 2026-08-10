from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch


@dataclass(frozen=True)
class SAEAnalysis:
    state_dict: dict[str, torch.Tensor]
    activations: np.ndarray
    thresholds: np.ndarray
    top_indices: tuple[tuple[int, ...], ...]
    losses: tuple[float, ...]
