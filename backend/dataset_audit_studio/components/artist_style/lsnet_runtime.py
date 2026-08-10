from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import timm
import torch
from PIL import Image
from timm.data import create_transform, resolve_data_config
from torch import Tensor, nn

from dataset_audit_studio.components.artist_style._lsnet import lsnet_artist  # noqa: F401


def load_lsnet_feature_model(
    checkpoint_path: Path,
    device: torch.device,
) -> tuple[Callable[[Image.Image], Tensor], nn.Module]:
    with torch.serialization.safe_globals((argparse.Namespace,)):
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    state_dict = _checkpoint_state_dict(checkpoint)
    num_classes, feature_dim = _head_dimensions(state_dict)
    model = timm.create_model(
        "lsnet_xl_artist_448",
        pretrained=False,
        num_classes=num_classes,
        feature_dim=feature_dim,
    )
    expected = model.state_dict()
    compatible: dict[str, Tensor] = {}
    incompatible: list[str] = []
    for key, value in state_dict.items():
        if key in expected and expected[key].shape == value.shape:
            compatible[key] = value
        elif "head" not in key:
            incompatible.append(key)
    if incompatible:
        raise RuntimeError(
            "LSNet checkpoint is incompatible with the pinned architecture: "
            + ", ".join(incompatible[:5])
        )
    loaded = model.load_state_dict(compatible, strict=False)
    missing = [key for key in loaded.missing_keys if "head" not in key]
    if missing:
        raise RuntimeError(
            "LSNet checkpoint has missing non-head weights: " + ", ".join(missing[:5])
        )
    transform = create_transform(
        **resolve_data_config({"input_size": (3, 448, 448)}, model=model)
    )
    return transform, model.to(device).eval().requires_grad_(False)


def _checkpoint_state_dict(checkpoint: Any) -> dict[str, Tensor]:
    raw_state = (
        checkpoint.get("model", checkpoint.get("model_ema", checkpoint))
        if isinstance(checkpoint, dict)
        else checkpoint
    )
    if not isinstance(raw_state, dict) or not all(
        isinstance(key, str) and isinstance(value, Tensor) for key, value in raw_state.items()
    ):
        raise RuntimeError("LSNet checkpoint does not contain a tensor state dictionary")
    return {
        key.removeprefix("module."): value
        for key, value in raw_state.items()
    }


def _head_dimensions(state_dict: dict[str, Tensor]) -> tuple[int, int]:
    num_classes = next(
        (
            int(value.shape[0])
            for key, value in state_dict.items()
            if key.endswith("head.l.weight") or key.endswith("head.weight")
        ),
        None,
    )
    feature_dim = next(
        (
            int(value.shape[0])
            for key, value in state_dict.items()
            if key.endswith("head.bn.weight")
        ),
        None,
    )
    if num_classes is None or feature_dim is None:
        raise RuntimeError("Unable to infer LSNet head dimensions from checkpoint")
    return num_classes, feature_dim
