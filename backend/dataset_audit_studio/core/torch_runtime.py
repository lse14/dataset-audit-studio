from __future__ import annotations

import gc
from contextlib import nullcontext
from typing import Literal

import numpy as np
import torch

DeviceRequest = Literal["auto", "cuda", "cpu"]
Precision = Literal["float32", "float16", "bfloat16"]


def resolve_torch_device(requested: DeviceRequest, precision: Precision) -> torch.device:
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA inference was requested but CUDA is unavailable")
    if requested == "auto":
        requested = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(requested)
    if device.type == "cpu" and precision != "float32":
        raise RuntimeError("CPU inference only supports float32 precision")
    return device


def autocast_context(device: torch.device, precision: Precision):
    if device.type != "cuda" or precision == "float32":
        return nullcontext()
    dtype = {
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
    }[precision]
    return torch.autocast(device_type="cuda", dtype=dtype)


def copy_float_features_to_tensor(values: np.ndarray, *, device: torch.device) -> torch.Tensor:
    """Copy immutable NumPy features before passing them to PyTorch."""
    return torch.from_numpy(np.array(values, dtype=np.float32, copy=True)).to(device)


def release_torch_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
