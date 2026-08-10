from __future__ import annotations

import hashlib

import pytest
from dataset_audit_studio.scoring.assets import build_component_identities
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.policy import evidence_for_result
from dataset_audit_studio.scoring.types import AssetFile, ModelAsset, RuntimeAssets
from pydantic import ValidationError


def _asset(model_id: str, *paths: str) -> ModelAsset:
    files = tuple(
        AssetFile(
            path=path,
            size=index + 1,
            sha256=hashlib.sha256(f"{model_id}:{path}".encode()).hexdigest(),
            mtime_ns=1,
        )
        for index, path in enumerate(paths)
    )
    return ModelAsset(
        model_id=model_id,
        loader="test_loader",
        root=f"E:\\models\\{model_id}",
        files=files,
        dependencies=(),
        is_custom=False,
        base_model_id=None,
    )


def _assets() -> RuntimeAssets:
    return RuntimeAssets(
        models_root="E:\\models",
        models=(
            _asset("aesthetic_lse14_5k", "5kdataset.safetensors"),
            _asset("jtp3_hydra", "models/jtp-3-hydra.safetensors"),
            _asset("waifu_scorer_v3", "model.safetensors"),
            _asset("openai_clip_vit_l14", "ViT-L-14.pt"),
            _asset("universal_fake_detector_head", "fc_weights.pth"),
            _asset("ppocrv5_server_det", "model.safetensors"),
            _asset("ppocrv5_server_rec", "model.safetensors"),
            _asset("watermark_siglip2", "model.safetensors"),
        ),
    )


def test_scoring_config_is_explicit_and_rejects_inverted_ai_thresholds() -> None:
    assert ScoringConfig.from_task_config({}).enabled_components == ()
    configured = ScoringConfig.from_task_config(
        {
            "scoring": {
                "aesthetic": {"enabled": True},
                "ai": {"enabled": True},
            }
        }
    )
    assert configured.enabled_components == ("aesthetic", "ai")

    with pytest.raises(ValidationError, match="candidate_threshold"):
        ScoringConfig.from_task_config(
            {
                "scoring": {
                    "ai": {
                        "enabled": True,
                        "candidate_threshold": 0.8,
                        "reference_threshold": 0.5,
                    }
                }
            }
        )


def test_component_identity_covers_every_exact_asset_hash() -> None:
    config = ScoringConfig.from_task_config(
        {
            "scoring": {
                "aesthetic": {"enabled": True},
                "ai": {"enabled": True},
                "ocr": {"enabled": True},
                "watermark": {"enabled": True},
            }
        }
    )
    identities = build_component_identities(config, _assets())
    assert set(identities) == {"aesthetic", "ai", "ocr", "watermark"}
    assert len({identity.model_sha256 for identity in identities.values()}) == 4
    assert all(len(identity.model_sha256) == 64 for identity in identities.values())


def test_policy_outputs_only_the_agreed_aesthetic_and_domain_dimensions() -> None:
    config = ScoringConfig.from_task_config(
        {"scoring": {"aesthetic": {"enabled": True, "in_domain_threshold": 0.5}}}
    )
    identity = build_component_identities(config, _assets())["aesthetic"]
    evidence = evidence_for_result(
        "aesthetic",
        {
            "aesthetic": 4.25,
            "in_domain_prob": 0.3,
            "composition": 4.9,
            "color": 4.8,
            "sexual": 1.2,
        },
        config,
        identity,
    )
    assert [item.code for item in evidence] == [
        "aesthetic_score",
        "in_domain_probability",
    ]
    assert evidence[1].severity == "high"
    assert evidence[1].metadata["in_domain_pass"] is False


def test_ai_and_watermark_are_review_only_evidence() -> None:
    config = ScoringConfig.from_task_config(
        {
            "scoring": {
                "ai": {"enabled": True},
                "watermark": {"enabled": True, "review_threshold": 0.75},
            }
        }
    )
    identities = build_component_identities(config, _assets())
    ai = evidence_for_result("ai", {"probability": 0.7}, config, identities["ai"])[0]
    watermark = evidence_for_result(
        "watermark",
        {
            "watermark_probability": 0.8,
            "probabilities": {"No Watermark": 0.2, "Watermark": 0.8},
        },
        config,
        identities["watermark"],
    )[0]
    assert ai.review_only is True and ai.metadata["candidate"] is True
    assert watermark.review_only is True and watermark.metadata["candidate"] is True
