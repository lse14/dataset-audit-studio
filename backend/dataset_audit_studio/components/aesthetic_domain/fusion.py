from __future__ import annotations

import json
from pathlib import Path

import torch
import torch.nn.functional as functional
from safetensors import safe_open
from safetensors.torch import load_file
from torch import Tensor, nn

from dataset_audit_studio.components.aesthetic_domain.validation import (
    validate_fusion_model,
)


class WaifuV3Head(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(768, 2048),
            nn.ReLU(),
            nn.BatchNorm1d(2048),
            nn.ReLU(),
            nn.Linear(2048, 512),
            nn.ReLU(),
            nn.BatchNorm1d(512),
            nn.ReLU(),
            nn.Linear(512, 256),
            nn.ReLU(),
            nn.BatchNorm1d(256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.Linear(128, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, features: Tensor) -> Tensor:
        return self.layers(features)


class FusionHead(nn.Module):
    def __init__(
        self,
        *,
        input_dim: int,
        hidden_dims: tuple[int, ...],
        dropout: float,
        regression_heads: tuple[str, ...],
        has_domain_head: bool,
    ) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        previous = input_dim
        for hidden in hidden_dims:
            layers.extend(
                (
                    nn.LayerNorm(previous),
                    nn.Linear(previous, hidden),
                    nn.GELU(),
                    nn.Dropout(dropout),
                )
            )
            previous = hidden
        self.trunk = nn.Sequential(*layers)
        self.reg_heads = nn.ModuleDict(
            {name: nn.Linear(previous, 1) for name in regression_heads}
        )
        self.cls_head = nn.Linear(previous, 1) if has_domain_head else None

    def forward(self, features: Tensor) -> tuple[Tensor, Tensor | None]:
        hidden = self.trunk(features)
        aesthetic = functional.sigmoid(self.reg_heads["aesthetic"](hidden)) * 4.0 + 1.0
        domain = self.cls_head(hidden).squeeze(-1) if self.cls_head is not None else None
        return aesthetic.squeeze(-1), domain


def load_waifu_head(path: Path, *, device: torch.device) -> WaifuV3Head:
    model = WaifuV3Head()
    model.load_state_dict(load_file(str(path), device="cpu"), strict=True)
    return model.eval().requires_grad_(False).to(device=device, dtype=torch.float32)


def load_fusion_head(path: Path, *, device: torch.device) -> FusionHead:
    summary = validate_fusion_model(path)
    with safe_open(str(path), framework="pt", device="cpu") as tensors:
        metadata = dict(tensors.metadata() or {})
        keys = set(tensors.keys())
        state = {key: tensors.get_tensor(key).float() for key in keys}
    hidden_dims = tuple(int(value) for value in json.loads(metadata["hidden_dims_json"]))
    regression_heads = tuple(
        name
        for name in ("aesthetic", "composition", "color", "sexual")
        if f"reg_heads.{name}.weight" in keys
    )
    model = FusionHead(
        input_dim=summary.input_dim,
        hidden_dims=hidden_dims,
        dropout=float(metadata.get("dropout", "0")),
        regression_heads=regression_heads,
        has_domain_head=summary.has_in_domain_head,
    )
    model.load_state_dict(state, strict=True)
    del state
    return model.eval().requires_grad_(False).to(device=device, dtype=torch.float32)
