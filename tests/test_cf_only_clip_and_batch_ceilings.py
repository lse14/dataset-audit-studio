from __future__ import annotations

import pytest
from dataset_audit_studio.clustering.config import ClusteringConfig
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.components.clip_features.config import ClipFeatureConfig
from dataset_audit_studio.components.clip_features.runtime import ClipFeatureRuntime
from dataset_audit_studio.components.semantic_embedding.config import SemanticEmbeddingConfig
from dataset_audit_studio.core.model_assets import RuntimeAssets
from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scoring.assets import CLIP_MODEL_ID, requested_model_ids
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.torch_runtime import TorchScoringRuntime
from pydantic import ValidationError


def test_cf_only_requested_models_and_runtime_exclude_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ScoringConfig.from_task_config(
        {
            "scoring": {
                "ai": {
                    "enabled": True,
                    "model_id": "community_forensics_model_384",
                }
            }
        }
    )
    assert CLIP_MODEL_ID not in requested_model_ids(config)
    assert requested_model_ids(config) == ("community_forensics_model_384",)

    monkeypatch.setattr(
        "dataset_audit_studio.scoring.torch_runtime.verify_runtime_asset_snapshot",
        lambda _assets: None,
    )

    class FakeAIRuntime:
        def __init__(self, _config, _assets) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "dataset_audit_studio.scoring.torch_runtime.AIDetectionRuntime",
        FakeAIRuntime,
    )
    monkeypatch.setattr(
        ClipFeatureRuntime,
        "__init__",
        lambda *_args, **_kwargs: pytest.fail("CF-only must not construct CLIP runtime"),
    )

    runtime = TorchScoringRuntime(config, RuntimeAssets(models_root=".", models=()))
    try:
        assert runtime.clip_runtime is None
    finally:
        runtime.close()


def test_batch_field_ceilings_rise_without_changing_defaults() -> None:
    assert ClipFeatureConfig().batch_size == 4
    assert SemanticEmbeddingConfig().batch_size == 8
    assert StyleConfig().batch_size == 4
    assert ScoringConfig().batch_size == 1
    assert ScanConfig().batch_size == 64
    assert ClusteringConfig().embedding_batch_size == 8

    ClipFeatureConfig(batch_size=256)
    SemanticEmbeddingConfig(batch_size=256)
    StyleConfig(batch_size=64)
    ScoringConfig(batch_size=256)
    ScanConfig(batch_size=4096)
    ClusteringConfig(embedding_batch_size=256)

    with pytest.raises(ValidationError):
        ClipFeatureConfig(batch_size=257)
    with pytest.raises(ValidationError):
        SemanticEmbeddingConfig(batch_size=257)
    with pytest.raises(ValidationError):
        StyleConfig(batch_size=65)
    with pytest.raises(ValidationError):
        ScoringConfig(batch_size=257)
    with pytest.raises(ValidationError):
        ScanConfig(batch_size=4097)
    with pytest.raises(ValidationError):
        ClusteringConfig(embedding_batch_size=257)
