from __future__ import annotations

from dataset_audit_studio.app.runtime_tuning import (
    GIB,
    HardwareSnapshot,
    recommend_runtime_tuning,
)


def test_cpu_recommendation_uses_float32_and_conservative_batches() -> None:
    recommendation = recommend_runtime_tuning(
        HardwareSnapshot(
            cuda_available=False,
            free_vram_bytes=None,
            total_vram_bytes=None,
            available_memory_bytes=32 * GIB,
        )
    )

    assert recommendation["device"] == "cpu"
    assert recommendation["precision"] == "float32"
    assert recommendation["updates"]["feature.clip_l14"] == {
        "device": "cpu",
        "precision": "float32",
        "batch_size": 8,
    }
    assert recommendation["updates"]["analysis.sae"]["batch_size"] == 2048


def test_cuda_recommendation_is_limited_by_the_lower_memory_capacity() -> None:
    recommendation = recommend_runtime_tuning(
        HardwareSnapshot(
            cuda_available=True,
            free_vram_bytes=24 * GIB,
            total_vram_bytes=24 * GIB,
            available_memory_bytes=8 * GIB,
        )
    )

    assert recommendation["device"] == "cuda"
    assert recommendation["precision"] == "float16"
    assert recommendation["updates"]["feature.clip_l14"]["batch_size"] == 8
    assert recommendation["updates"]["evidence.ocr"]["recognition_batch_size"] == 16


def test_low_memory_cuda_recommendation_stays_within_component_minimums() -> None:
    recommendation = recommend_runtime_tuning(
        HardwareSnapshot(
            cuda_available=True,
            free_vram_bytes=GIB,
            total_vram_bytes=8 * GIB,
            available_memory_bytes=2 * GIB,
        )
    )

    assert recommendation["updates"]["feature.clip_l14"]["batch_size"] == 1
    assert recommendation["updates"]["evidence.ocr"]["recognition_batch_size"] == 4
    assert recommendation["updates"]["style.artist"]["batch_size"] == 1
