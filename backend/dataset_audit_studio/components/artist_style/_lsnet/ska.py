from __future__ import annotations

import math

import torch
import torch.nn.functional as functional
from torch import nn


class SKA(nn.Module):
    """PyTorch implementation of the pinned LSNet SKA operation.

    The upstream implementation optionally accelerates this operation with Triton.
    Triton has no supported project runtime on Windows, so this equivalent inference
    path keeps the model architecture importable on every supported device.
    """

    def forward(self, values: torch.Tensor, weights: torch.Tensor) -> torch.Tensor:
        kernel_size = int(math.sqrt(weights.shape[2]))
        padding = (kernel_size - 1) // 2
        batch, input_channels, height, width = values.shape
        weight_channels = weights.shape[1]
        unfolded = functional.unfold(values, kernel_size=kernel_size, padding=padding).view(
            batch, input_channels, kernel_size * kernel_size, height * width
        )
        expanded_weights = weights.view(
            batch, weight_channels, kernel_size * kernel_size, height * width
        )
        if input_channels != weight_channels:
            if input_channels % weight_channels:
                raise ValueError("LSNet SKA channels are not divisible by weight channels")
            expanded_weights = expanded_weights.repeat(1, input_channels // weight_channels, 1, 1)
        return (unfolded * expanded_weights).sum(dim=2).view(
            batch, input_channels, height, width
        )
