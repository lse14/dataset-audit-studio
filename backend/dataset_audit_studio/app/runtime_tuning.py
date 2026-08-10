from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Any, Literal

import torch

RuntimeDevice = Literal["cpu", "cuda"]
RuntimePrecision = Literal["float32", "float16"]

GIB = 1024**3


@dataclass(frozen=True)
class HardwareSnapshot:
    cuda_available: bool
    free_vram_bytes: int | None
    total_vram_bytes: int | None
    available_memory_bytes: int | None

    def public_dict(self) -> dict[str, int | bool | None]:
        return {
            "cuda_available": self.cuda_available,
            "free_vram_bytes": self.free_vram_bytes,
            "total_vram_bytes": self.total_vram_bytes,
            "available_memory_bytes": self.available_memory_bytes,
        }


def hardware_snapshot() -> HardwareSnapshot:
    cuda_available = torch.cuda.is_available()
    free_vram_bytes: int | None = None
    total_vram_bytes: int | None = None
    if cuda_available:
        try:
            free_vram_bytes, total_vram_bytes = torch.cuda.mem_get_info()
        except RuntimeError:
            cuda_available = False
    return HardwareSnapshot(
        cuda_available=cuda_available,
        free_vram_bytes=free_vram_bytes,
        total_vram_bytes=total_vram_bytes,
        available_memory_bytes=_available_memory_bytes(),
    )


def recommend_runtime_tuning(snapshot: HardwareSnapshot) -> dict[str, Any]:
    device: RuntimeDevice = "cuda" if snapshot.cuda_available else "cpu"
    precision: RuntimePrecision = "float16" if snapshot.cuda_available else "float32"
    tier = _capacity_tier(snapshot)
    return {
        "hardware": snapshot.public_dict(),
        "device": device,
        "precision": precision,
        "updates": {
            "feature.clip_l14": {
                "device": device,
                "precision": precision,
                "batch_size": _tier_value(tier, (1, 4, 8, 16, 32)),
            },
            "score.aesthetic_domain": {"device": device, "precision": precision},
            "detect.ai": {"device": device, "precision": precision},
            "evidence.ocr": {
                "device": device,
                "precision": precision,
                "recognition_batch_size": _tier_value(tier, (4, 8, 16, 32, 64)),
            },
            "evidence.watermark": {"device": device, "precision": precision},
            "style.artist": {
                "device": device,
                "batch_size": _tier_value(tier, (1, 2, 4, 8, 16)),
            },
            "embedding.semantic": {
                "device": device,
                "batch_size": _tier_value(tier, (2, 4, 8, 16, 32)),
            },
            "analysis.sae": {
                "batch_size": _tier_value(
                    _memory_tier(snapshot.available_memory_bytes),
                    (128, 256, 512, 1024, 2048),
                ),
            },
        },
    }


def _capacity_tier(snapshot: HardwareSnapshot) -> int:
    if not snapshot.cuda_available:
        return min(_memory_tier(snapshot.available_memory_bytes), 2)
    vram_tier = _memory_tier(snapshot.free_vram_bytes)
    memory_tier = _memory_tier(snapshot.available_memory_bytes)
    return min(vram_tier, memory_tier)


def _memory_tier(value: int | None) -> int:
    if value is None:
        return 0
    if value >= 24 * GIB:
        return 4
    if value >= 16 * GIB:
        return 3
    if value >= 8 * GIB:
        return 2
    if value >= 4 * GIB:
        return 1
    return 0


def _tier_value(tier: int, values: tuple[int, int, int, int, int]) -> int:
    return values[tier]


def _available_memory_bytes() -> int | None:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.available_physical)
    except (AttributeError, OSError):
        pass
    return None
