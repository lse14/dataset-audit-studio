from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from pathlib import Path

from safetensors import SafetensorError, safe_open

from dataset_audit_studio.model_adapters.errors import ModelIntegrityError, ModelSchemaError
from dataset_audit_studio.model_adapters.types import RegistryFile, TensorSummary

FLOAT_DTYPES = frozenset({"BF16", "F16", "F32", "F64"})


def sha256_file(
    path: Path,
    *,
    progress: Callable[[int], None] | None = None,
    chunk_size: int = 4 * 1024 * 1024,
) -> str:
    digest = hashlib.sha256()
    processed = 0
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
            processed += len(chunk)
            if progress is not None:
                progress(processed)
    return digest.hexdigest()


def validate_expected_file(
    path: Path,
    expected: RegistryFile,
    *,
    progress: Callable[[int], None] | None = None,
) -> None:
    stat_result = path.stat()
    if stat_result.st_size != expected.size:
        raise ModelIntegrityError(
            f"Model file size mismatch for {expected.path}: "
            f"expected {expected.size}, got {stat_result.st_size}"
        )
    digest = sha256_file(path, progress=progress)
    if digest != expected.sha256:
        raise ModelIntegrityError(
            f"Model file SHA-256 mismatch for {expected.path}: "
            f"expected {expected.sha256}, got {digest}"
        )
    validate_file_container(path, expected.format)


def validate_file_container(path: Path, file_format: str) -> None:
    if file_format == "json":
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModelSchemaError(f"Invalid JSON model file {path.name}: {error}") from error
        if not isinstance(parsed, dict):
            raise ModelSchemaError(f"JSON model file must contain an object: {path.name}")
    elif file_format == "safetensors":
        inspect_safetensors(path)


def inspect_safetensors(path: Path) -> tuple[dict[str, str], dict[str, tuple[int, ...]]]:
    try:
        with safe_open(str(path), framework="numpy") as tensors:
            keys = tuple(tensors.keys())
            if not keys:
                raise ModelSchemaError(f"Safetensors file contains no tensors: {path.name}")
            dtypes = {key: tensors.get_slice(key).get_dtype() for key in keys}
            shapes = {
                key: tuple(int(value) for value in tensors.get_slice(key).get_shape())
                for key in keys
            }
    except (OSError, SafetensorError) as error:
        raise ModelSchemaError(f"Invalid safetensors container {path.name}: {error}") from error
    return dtypes, shapes


def validate_lse14_replacement(path: Path) -> TensorSummary:
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
        raise ModelSchemaError(f"Invalid safetensors replacement: {error}") from error

    model_format = metadata.get("format")
    if model_format != "fusion_multitask_v1":
        raise ModelSchemaError("Aesthetic replacement metadata format must be fusion_multitask_v1")
    try:
        input_dim = int(metadata["input_dim"])
    except (KeyError, TypeError, ValueError) as error:
        raise ModelSchemaError(
            "Aesthetic replacement metadata requires integer input_dim"
        ) from error
    if input_dim != 8273:
        raise ModelSchemaError(f"Aesthetic replacement input_dim must be 8273, got {input_dim}")

    hidden_raw = metadata.get("hidden_dims_json") or metadata.get("hidden_dims")
    try:
        hidden_parsed = json.loads(hidden_raw or "")
        hidden_dims = tuple(int(value) for value in hidden_parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ModelSchemaError(
            "Aesthetic replacement requires hidden_dims_json metadata"
        ) from error
    if not 1 <= len(hidden_dims) <= 8 or any(value <= 0 or value > 16384 for value in hidden_dims):
        raise ModelSchemaError(
            "Aesthetic replacement hidden dimensions are outside supported bounds"
        )

    try:
        config = json.loads(metadata["config_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ModelSchemaError(
            "Aesthetic replacement requires valid config_json metadata"
        ) from error
    if not isinstance(config, dict):
        raise ModelSchemaError("Aesthetic replacement config_json must contain an object")
    model_config = config.get("models")
    if not isinstance(model_config, dict):
        raise ModelSchemaError("Aesthetic replacement config_json requires a models object")
    jtp3_id = str(model_config.get("jtp3_model_id", "")).strip().casefold()
    if jtp3_id not in {"redrocket/hydra", "redrocket/jtp-3"}:
        raise ModelSchemaError("Aesthetic replacement must use the registered JTP-3/Hydra feature")
    clip_name = str(model_config.get("waifu_clip_model_name", "")).strip().casefold()
    clip_pretrained = str(model_config.get("waifu_clip_pretrained", "")).strip().casefold()
    if clip_name != "vit-l-14" or clip_pretrained != "openai":
        raise ModelSchemaError("Aesthetic replacement must use OpenAI CLIP ViT-L-14")
    if model_config.get("include_waifu_score") is not True:
        raise ModelSchemaError("Aesthetic replacement must include the Waifu V3 score feature")

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

    optional_groups = {
        "cls_head": {
            "cls_head.weight": (1, previous),
            "cls_head.bias": (1,),
        },
        "composition": {
            "reg_heads.composition.weight": (1, previous),
            "reg_heads.composition.bias": (1,),
        },
        "color": {
            "reg_heads.color.weight": (1, previous),
            "reg_heads.color.bias": (1,),
        },
        "sexual": {
            "reg_heads.sexual.weight": (1, previous),
            "reg_heads.sexual.bias": (1,),
        },
    }
    allowed = set(expected)
    for group in optional_groups.values():
        present = set(group) & keys
        if present and present != set(group):
            raise ModelSchemaError("Optional aesthetic tensor groups must be complete")
        if present:
            expected.update(group)
        allowed.update(group)

    missing = set(expected) - keys
    extra = keys - allowed
    if missing:
        raise ModelSchemaError(f"Aesthetic replacement is missing tensors: {sorted(missing)}")
    if extra:
        raise ModelSchemaError(f"Aesthetic replacement has unsupported tensors: {sorted(extra)}")
    for key, expected_shape in expected.items():
        if shapes[key] != expected_shape:
            raise ModelSchemaError(
                f"Aesthetic tensor {key} has shape {shapes[key]}, expected {expected_shape}"
            )
        if dtypes[key] not in FLOAT_DTYPES:
            raise ModelSchemaError(
                f"Aesthetic tensor {key} must use a floating dtype, got {dtypes[key]}"
            )

    dropout_raw = metadata.get("dropout", "0")
    try:
        dropout = float(dropout_raw)
    except ValueError as error:
        raise ModelSchemaError("Aesthetic replacement dropout metadata must be numeric") from error
    if not math.isfinite(dropout) or not 0 <= dropout < 1:
        raise ModelSchemaError("Aesthetic replacement dropout must be in [0, 1)")

    return TensorSummary(
        format=model_format,
        input_dim=input_dim,
        hidden_dims=hidden_dims,
        has_in_domain_head={"cls_head.weight", "cls_head.bias"}.issubset(keys),
        tensor_count=len(keys),
        metadata={
            "dropout": dropout,
            "jtp3_model_id": model_config.get("jtp3_model_id"),
            "target_dims": list(config.get("training", {}).get("target_dims", []))
            if isinstance(config.get("training"), dict)
            else [],
            "waifu_clip_model_name": model_config.get("waifu_clip_model_name"),
            "waifu_clip_pretrained": model_config.get("waifu_clip_pretrained"),
        },
    )

