from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch
from dataset_audit_studio.components.artist_style.assets import (
    DINO_MODEL_ID,
    LSNET_MODEL_ID,
    VGG_MODEL_ID,
    requested_style_model_ids,
)
from dataset_audit_studio.components.artist_style.contracts import StyleSample
from dataset_audit_studio.components.artist_style.manifest import DEFINITION
from dataset_audit_studio.components.artist_style.runtime import TorchStyleRuntime
from dataset_audit_studio.core.component_contracts import NormalizedComponentConfig
from dataset_audit_studio.core.model_assets import RuntimeAssets
from dataset_audit_studio.style.analysis import analyze_artist_scope
from dataset_audit_studio.style.config import StyleConfig
from dataset_audit_studio.style.torch_runtime import (
    extract_vgg19_gram_embeddings,
    gram_matrix_batch,
)
from PIL import Image
from pydantic import ValidationError
from torch import nn
from torchvision import models


@pytest.mark.parametrize(
    ("config", "expected"),
    (
        (
            StyleConfig(lsnet_weight=0.0, gram_weight=0.8, dino_weight=0.2),
            (VGG_MODEL_ID, DINO_MODEL_ID),
        ),
        (
            StyleConfig(lsnet_weight=0.4, gram_weight=0.0, dino_weight=0.6),
            (LSNET_MODEL_ID, DINO_MODEL_ID),
        ),
        (
            StyleConfig(lsnet_weight=0.4, gram_weight=0.6, dino_weight=0.0),
            (LSNET_MODEL_ID, VGG_MODEL_ID),
        ),
    ),
)
def test_style_model_requests_follow_positive_weights(
    config: StyleConfig,
    expected: tuple[str, ...],
) -> None:
    assert requested_style_model_ids(config) == expected
    normalized = NormalizedComponentConfig(
        component_id="style.artist",
        enabled=True,
        config=config.model_dump(mode="python"),
    )
    assert DEFINITION.model_ids(normalized) == expected


def test_style_config_uses_tuned_default_outlier_settings() -> None:
    config = StyleConfig.from_task_config({})
    assert config.max_iterations == 3
    assert config.outlier_sigma == 0.522
    assert config.minimum_style_score == 92.07
    assert config.lsnet_weight == 0.892
    assert config.gram_weight == 0.0
    assert config.dino_weight == 0.108
    assert config.gram_average_weight == 0.8
    assert config.gram_centroid_weight == 0.2
    assert requested_style_model_ids(config) == (LSNET_MODEL_ID, DINO_MODEL_ID)
    with pytest.raises(ValidationError, match="must sum to 1"):
        StyleConfig.from_task_config(
            {
                "style": {
                    "lsnet_weight": 0.4,
                    "gram_weight": 0.5,
                    "dino_weight": 0.2,
                }
            }
        )
    legacy = StyleConfig.from_task_config(
        {"style": {"gram_weight": 0.8, "dino_weight": 0.2}}
    )
    assert legacy.lsnet_weight == 0.0


def test_zero_lsnet_weight_skips_model_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataset_audit_studio.components.artist_style.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "verify_runtime_asset_snapshot", lambda _assets: None)
    monkeypatch.setattr(runtime_module, "resolve_torch_device", lambda *_args: torch.device("cpu"))
    monkeypatch.setattr(
        TorchStyleRuntime,
        "_load_lsnet",
        lambda _self: pytest.fail("LSNet should be lazy"),
    )
    monkeypatch.setattr(TorchStyleRuntime, "_load_vgg", lambda _self: object())
    monkeypatch.setattr(
        TorchStyleRuntime,
        "_load_dino",
        lambda _self: (SimpleNamespace(image_mean=(0.5,) * 3, image_std=(0.5,) * 3), object()),
    )

    runtime = TorchStyleRuntime(
        StyleConfig(lsnet_weight=0.0, gram_weight=0.8, dino_weight=0.2),
        RuntimeAssets(models_root=".", models=()),
    )

    assert runtime.lsnet is None
    assert runtime.lsnet_transform is None


@pytest.mark.parametrize(
    ("config", "skipped_model"),
    (
        (
            StyleConfig(lsnet_weight=0.0, gram_weight=0.8, dino_weight=0.2),
            "lsnet",
        ),
        (
            StyleConfig(lsnet_weight=0.4, gram_weight=0.0, dino_weight=0.6),
            "vgg",
        ),
        (
            StyleConfig(lsnet_weight=0.4, gram_weight=0.6, dino_weight=0.0),
            "dino",
        ),
    ),
)
def test_zero_weight_style_model_is_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
    config: StyleConfig,
    skipped_model: str,
) -> None:
    import dataset_audit_studio.components.artist_style.runtime as runtime_module

    loaded: list[str] = []

    def load_lsnet(_self):
        loaded.append("lsnet")
        return lambda _image: torch.zeros((3, 224, 224)), SimpleNamespace()

    def load_vgg(_self):
        loaded.append("vgg")
        return object()

    def load_dino(_self):
        loaded.append("dino")
        return SimpleNamespace(image_mean=(0.5,) * 3, image_std=(0.5,) * 3), object()

    monkeypatch.setattr(runtime_module, "verify_runtime_asset_snapshot", lambda _assets: None)
    monkeypatch.setattr(runtime_module, "resolve_torch_device", lambda *_args: torch.device("cpu"))
    monkeypatch.setattr(TorchStyleRuntime, "_load_lsnet", load_lsnet)
    monkeypatch.setattr(TorchStyleRuntime, "_load_vgg", load_vgg)
    monkeypatch.setattr(TorchStyleRuntime, "_load_dino", load_dino)

    TorchStyleRuntime(config, RuntimeAssets(models_root=".", models=()))

    assert skipped_model not in loaded


def test_skipped_style_models_emit_neutral_feature_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import dataset_audit_studio.components.artist_style.runtime as runtime_module

    class FakeLsnet:
        def __call__(self, batch, *, return_features):
            assert return_features is True
            return torch.ones((batch.shape[0], 2))

    monkeypatch.setattr(runtime_module, "verify_runtime_asset_snapshot", lambda _assets: None)
    monkeypatch.setattr(runtime_module, "resolve_torch_device", lambda *_args: torch.device("cpu"))
    monkeypatch.setattr(
        TorchStyleRuntime,
        "_load_lsnet",
        lambda _self: (lambda _image: torch.zeros((3, 224, 224)), FakeLsnet()),
    )
    monkeypatch.setattr(
        TorchStyleRuntime,
        "_load_vgg",
        lambda _self: pytest.fail("VGG must not load when gram_weight is zero"),
    )
    monkeypatch.setattr(
        TorchStyleRuntime,
        "_load_dino",
        lambda _self: pytest.fail("DINO must not load when dino_weight is zero"),
    )

    image_path = tmp_path / "sample.png"
    Image.new("RGB", (8, 8), (128, 64, 32)).save(image_path)
    sample = StyleSample(
        sample_id="sample-1",
        relative_path="sample.png",
        artist_scope="artist",
        source_path=image_path,
        image_path=image_path,
        source_size=image_path.stat().st_size,
        source_mtime_ns=image_path.stat().st_mtime_ns,
        pixel_sha256="a" * 64,
    )

    runtime = TorchStyleRuntime(
        StyleConfig(lsnet_weight=1.0, gram_weight=0.0, dino_weight=0.0),
        RuntimeAssets(models_root=str(tmp_path), models=()),
    )
    batch = runtime.extract((sample,))

    assert batch.lsnet.shape == (1, 2)
    assert batch.gram.shape == (1, 1)
    assert batch.dino.shape == (1, 1)
    assert np.array_equal(batch.gram, np.ones((1, 1), dtype=np.float32))
    assert np.array_equal(batch.dino, np.ones((1, 1), dtype=np.float32))
    runtime.close()


def test_gram_matrix_uses_channel_spatial_normalization() -> None:
    feature_map = torch.tensor(
        [[[[1.0, 2.0], [3.0, 4.0]], [[2.0, 0.0], [1.0, 3.0]]]]
    )
    flattened = feature_map.reshape(1, 2, 4)
    expected = torch.bmm(flattened, flattened.transpose(1, 2)) / 8.0
    assert torch.equal(gram_matrix_batch(feature_map), expected)


def test_vgg19_reference_layers_produce_pooled_normalized_features() -> None:
    torch.manual_seed(0)
    feature_model: nn.Module = models.vgg19(weights=None).features.eval()
    batch = torch.rand((1, 3, 224, 224), dtype=torch.float32)
    with torch.inference_mode():
        embeddings = extract_vgg19_gram_embeddings(batch, feature_model)
    assert embeddings.shape == (1, 1_024)
    assert torch.allclose(embeddings.norm(dim=1), torch.ones(1), atol=1e-6, rtol=0)


def _scope_features(
    count: int,
) -> tuple[tuple[str, ...], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    sample_ids = tuple(f"sample-{index}" for index in range(count))
    lsnet = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (count, 1))
    gram = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (count, 1))
    dino = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (count, 1))
    colors = np.tile(np.array([[0.5, 0.5]], dtype=np.float32), (count, 1))
    lsnet[-1] = (-1.0, 0.0)
    gram[-1] = (-1.0, 0.0)
    return sample_ids, lsnet, gram, dino, colors


def test_iterative_artist_core_removes_strong_gram_outlier() -> None:
    sample_ids, lsnet, gram, dino, colors = _scope_features(8)
    results = analyze_artist_scope(
        sample_ids,
        lsnet,
        gram,
        dino,
        colors,
        StyleConfig(lsnet_weight=0.8, gram_weight=0.2, dino_weight=0.0),
    )
    assert [result.sample_id for result in results if result.strong_outlier] == [
        "sample-7"
    ]
    assert all(result.core_member for result in results[:7])
    assert results[-1].core_member is False
    assert results[-1].iteration_removed == 1
    assert results[-1].outlier_reason == "lsnet_and_gram_similarity_below_scope_threshold"


def test_small_artist_scope_marks_candidate_without_automatic_removal() -> None:
    sample_ids, lsnet, gram, dino, colors = _scope_features(7)
    results = analyze_artist_scope(
        sample_ids,
        lsnet,
        gram,
        dino,
        colors,
        StyleConfig(),
    )
    assert all(result.core_member for result in results)
    assert not any(result.strong_outlier for result in results)
    assert results[-1].review_required is True
    assert results[-1].outlier_reason == "small_scope_review"


def test_dino_guardrail_does_not_override_unanimous_gram_style() -> None:
    count = 8
    sample_ids = tuple(f"sample-{index}" for index in range(count))
    lsnet = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (count, 1))
    gram = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (count, 1))
    dino = gram.copy()
    dino[-1] = (-1.0, 0.0)
    colors = np.tile(np.array([[0.5, 0.5]], dtype=np.float32), (count, 1))
    results = analyze_artist_scope(
        sample_ids,
        lsnet,
        gram,
        dino,
        colors,
        StyleConfig(
            max_iterations=3,
            outlier_sigma=2.0,
            minimum_style_score=50.0,
            lsnet_weight=0.4,
            gram_weight=0.4,
            dino_weight=0.2,
            gram_average_weight=0.75,
            gram_centroid_weight=0.25,
        ),
    )
    assert all(result.core_member for result in results)
    assert results[-1].dino_guardrail_score < 100.0
    assert results[-1].style_score >= 80.0


def test_lsnet_weight_changes_per_image_score() -> None:
    sample_ids, lsnet, gram, dino, colors = _scope_features(8)
    lsnet[-1] = (1.0, 0.0)
    lsnet_only = analyze_artist_scope(
        sample_ids,
        lsnet,
        gram,
        dino,
        colors,
        StyleConfig(lsnet_weight=0.8, gram_weight=0.0, dino_weight=0.2),
    )
    gram_only = analyze_artist_scope(
        sample_ids,
        lsnet,
        gram,
        dino,
        colors,
        StyleConfig(lsnet_weight=0.0, gram_weight=0.8, dino_weight=0.2),
    )
    assert lsnet_only[-1].style_score != gram_only[-1].style_score
