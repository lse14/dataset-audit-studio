from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from safetensors import SafetensorError, safe_open

FLOAT_DTYPES = frozenset({"BF16", "F16", "F32", "F64"})


@dataclass(frozen=True)
class FusionModelSummary:
    input_dim: int
    hidden_dims: tuple[int, ...]
    has_in_domain_head: bool


def validate_fusion_model(path: Path) -> FusionModelSummary:
    try:
        with safe_open(str(path), framework="numpy") as tensors:
            metadata = dict(tensors.metadata() or {})
            keys = set(tensors.keys())
            dtypes = {key: tensors.get_slice(key).get_dtype() for key in keys}
            shapes = {
                key: tuple(int(value) for value in tensors.get_slice(key).get_shape())
                for key in keys
            }
    except (OSError, SafetensorError) as error:
        raise ValueError(f"Invalid aesthetic fusion safetensors: {error}") from error
    if metadata.get("format") != "fusion_multitask_v1":
        raise ValueError("Aesthetic fusion format must be fusion_multitask_v1")
    try:
        input_dim = int(metadata["input_dim"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Aesthetic fusion requires integer input_dim") from error
    if input_dim != 8273:
        raise ValueError(f"Aesthetic fusion input_dim must be 8273, got {input_dim}")
    try:
        hidden_dims = tuple(
            int(value) for value in json.loads(metadata.get("hidden_dims_json", ""))
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Aesthetic fusion requires hidden_dims_json") from error
    if not 1 <= len(hidden_dims) <= 8 or any(
        value <= 0 or value > 16384 for value in hidden_dims
    ):
        raise ValueError("Aesthetic fusion hidden dimensions are outside supported bounds")
    try:
        config = json.loads(metadata["config_json"])
        model_config = config["models"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Aesthetic fusion requires config_json.models") from error
    if not isinstance(model_config, dict):
        raise ValueError("Aesthetic fusion config_json.models must be an object")
    jtp3_id = str(model_config.get("jtp3_model_id", "")).strip().casefold()
    if jtp3_id not in {"redrocket/hydra", "redrocket/jtp-3"}:
        raise ValueError("Aesthetic fusion must use the registered JTP-3/Hydra feature")
    clip_name = str(model_config.get("waifu_clip_model_name", "")).strip().casefold()
    clip_pretrained = str(
        model_config.get("waifu_clip_pretrained", "")
    ).strip().casefold()
    if clip_name != "vit-l-14" or clip_pretrained != "openai":
        raise ValueError("Aesthetic fusion must use OpenAI CLIP ViT-L-14")
    if model_config.get("include_waifu_score") is not True:
        raise ValueError("Aesthetic fusion must include the Waifu V3 score")

    expected: dict[str, tuple[int, ...]] = {}
    previous = input_dim
    for index, hidden in enumerate(hidden_dims):
        layer = index * 4
        expected[f"trunk.{layer}.weight"] = (previous,)
        expected[f"trunk.{layer}.bias"] = (previous,)
        expected[f"trunk.{layer + 1}.weight"] = (hidden, previous)
        expected[f"trunk.{layer + 1}.bias"] = (hidden,)
        previous = hidden
    expected["reg_heads.aesthetic.weight"] = (1, previous)
    expected["reg_heads.aesthetic.bias"] = (1,)
    optional_groups = (
        {
            "cls_head.weight": (1, previous),
            "cls_head.bias": (1,),
        },
        {
            "reg_heads.composition.weight": (1, previous),
            "reg_heads.composition.bias": (1,),
        },
        {
            "reg_heads.color.weight": (1, previous),
            "reg_heads.color.bias": (1,),
        },
        {
            "reg_heads.sexual.weight": (1, previous),
            "reg_heads.sexual.bias": (1,),
        },
    )
    allowed = set(expected)
    for group in optional_groups:
        present = set(group) & keys
        if present and present != set(group):
            raise ValueError("Optional aesthetic fusion tensor groups must be complete")
        if present:
            expected.update(group)
        allowed.update(group)
    missing = set(expected) - keys
    extra = keys - allowed
    if missing:
        raise ValueError(f"Aesthetic fusion is missing tensors: {sorted(missing)}")
    if extra:
        raise ValueError(f"Aesthetic fusion has unsupported tensors: {sorted(extra)}")
    for key, expected_shape in expected.items():
        if shapes[key] != expected_shape:
            raise ValueError(
                f"Aesthetic fusion tensor {key} has shape {shapes[key]}, "
                f"expected {expected_shape}"
            )
        if dtypes[key] not in FLOAT_DTYPES:
            raise ValueError(f"Aesthetic fusion tensor {key} is not floating point")
    try:
        dropout = float(metadata.get("dropout", "0"))
    except ValueError as error:
        raise ValueError("Aesthetic fusion dropout must be numeric") from error
    if not math.isfinite(dropout) or not 0 <= dropout < 1:
        raise ValueError("Aesthetic fusion dropout must be in [0, 1)")
    return FusionModelSummary(
        input_dim=input_dim,
        hidden_dims=hidden_dims,
        has_in_domain_head={"cls_head.weight", "cls_head.bias"}.issubset(keys),
    )
