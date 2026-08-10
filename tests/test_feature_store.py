from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from dataset_audit_studio.adapters.safetensor_features import SafetensorFeatureStore

TASK_ID = "00000000-0000-0000-0000-000000000001"


def test_feature_store_is_atomic_versioned_and_tamper_evident(tmp_path: Path) -> None:
    store = SafetensorFeatureStore(project_root=tmp_path)
    features = {
        "embedding.clip_l14.aesthetic.v1": np.asarray(
            [[1.0, 0.0], [0.0, 1.0]], dtype=np.float32
        ),
        "embedding.clip_l14.ufd.v1": np.asarray(
            [[2.0, 3.0], [4.0, 5.0]], dtype=np.float32
        ),
    }
    shard = store.write(
        task_id=TASK_ID,
        producer_id="feature.clip_l14",
        sample_ids=("sample-a", "sample-b"),
        pixel_hashes=("a" * 64, "b" * 64),
        features=features,
        model_digest="c" * 64,
        preprocessing_version="dual-preprocess-v1",
    )
    assert shard.capabilities == tuple(sorted(features))
    assert shard.dimensions == tuple((capability, 2) for capability in sorted(features))
    loaded = store.load(shard)
    assert loaded.sample_ids == ("sample-a", "sample-b")
    for capability, expected in features.items():
        assert np.array_equal(loaded.get(capability), expected)
    assert not list(tmp_path.rglob("*.part"))

    path = tmp_path.joinpath(*Path(shard.relative_path).parts)
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError, match="SHA-256 changed"):
        store.load(shard)


def test_feature_store_cache_key_changes_for_every_identity_boundary(tmp_path: Path) -> None:
    store = SafetensorFeatureStore(project_root=tmp_path)
    common = {
        "sample_ids": ("sample-a",),
        "pixel_hashes": ("a" * 64,),
        "capabilities": ("embedding.clip_l14.aesthetic.v1",),
        "model_digest": "b" * 64,
        "preprocessing_version": "v1",
    }
    baseline = store.cache_key(**common)
    variants = (
        {**common, "pixel_hashes": ("c" * 64,)},
        {**common, "capabilities": ("embedding.clip_l14.ufd.v1",)},
        {**common, "model_digest": "d" * 64},
        {**common, "preprocessing_version": "v2"},
    )
    assert all(store.cache_key(**variant) != baseline for variant in variants)

