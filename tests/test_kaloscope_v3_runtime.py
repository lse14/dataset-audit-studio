from __future__ import annotations

import pytest
import timm
import torch
from dataset_audit_studio.components.artist_style.kaloscope_v3_runtime import (
    KALOSCOPE_IMAGE_SIZE,
    NUM_REGISTER_TOKENS,
    TIMM_MODEL_NAME,
    _build_projector,
    _remap_state_dict,
    pool_feature_tokens,
)


def _backbone_state(blocks: int = 1, dim: int = 6) -> dict[str, torch.Tensor]:
    state: dict[str, torch.Tensor] = {
        "backbone.cls_token": torch.zeros((1, 1, dim)),
        "backbone.storage_tokens": torch.arange(1.0, 1.0 + 2 * dim).reshape(1, 2, dim),
        "backbone.mask_token": torch.zeros((1, dim)),
        "backbone.rope_embed.periods": torch.ones(4),
        "backbone.patch_embed.proj.weight": torch.zeros((dim, 3, 2, 2)),
        "backbone.patch_embed.proj.bias": torch.zeros(dim),
        "backbone.norm.weight": torch.zeros(dim),
        "backbone.norm.bias": torch.zeros(dim),
    }
    for index in range(blocks):
        prefix = f"backbone.blocks.{index}."
        state[prefix + "attn.qkv.weight"] = torch.zeros((3 * dim, dim))
        state[prefix + "attn.qkv.bias"] = torch.arange(
            0.0, 3.0 * dim, dtype=torch.float32
        )
        state[prefix + "attn.qkv.bias_mask"] = torch.zeros(3 * dim)
        state[prefix + "attn.proj.weight"] = torch.zeros((dim, dim))
        state[prefix + "attn.proj.bias"] = torch.zeros(dim)
        state[prefix + "ls1.gamma"] = torch.zeros(dim)
        state[prefix + "ls2.gamma"] = torch.zeros(dim)
        state[prefix + "mlp.fc1.weight"] = torch.zeros((dim, dim))
        state[prefix + "mlp.fc1.bias"] = torch.zeros(dim)
        state[prefix + "mlp.fc2.weight"] = torch.zeros((dim, dim))
        state[prefix + "mlp.fc2.bias"] = torch.zeros(dim)
        state[prefix + "norm1.weight"] = torch.zeros(dim)
        state[prefix + "norm1.bias"] = torch.zeros(dim)
        state[prefix + "norm2.weight"] = torch.zeros(dim)
        state[prefix + "norm2.bias"] = torch.zeros(dim)
    return state


def test_remap_splits_qkv_bias_into_q_and_v_bias() -> None:
    state = _backbone_state(blocks=2, dim=4)
    mapped = _remap_state_dict(state)

    # timm's qk-normalized variant requires both parameters to exist. Dropping the
    # fused bias instead of splitting it leaves 24 missing keys on the real model.
    assert "blocks.0.attn.q_bias" in mapped
    assert "blocks.0.attn.v_bias" in mapped
    assert "blocks.1.attn.q_bias" in mapped
    assert not any(key.endswith("qkv.bias") for key in mapped)

    fused = state["backbone.blocks.0.attn.qkv.bias"]
    query, _, value = fused.chunk(3, dim=0)
    assert torch.equal(mapped["blocks.0.attn.q_bias"], query)
    assert torch.equal(mapped["blocks.0.attn.v_bias"], value)


def test_remap_drops_mask_token_rope_periods_and_bias_mask_only() -> None:
    mapped = _remap_state_dict(_backbone_state())

    assert "mask_token" not in mapped
    assert "rope_embed.periods" not in mapped
    assert not any(key.endswith("bias_mask") for key in mapped)
    # Everything else under the backbone prefix survives the mapping.
    assert "cls_token" in mapped
    assert "patch_embed.proj.weight" in mapped
    assert "norm.weight" in mapped


def test_remap_renames_layer_scale_gammas_and_storage_tokens() -> None:
    mapped = _remap_state_dict(_backbone_state(dim=3))

    assert "blocks.0.gamma_1" in mapped
    assert "blocks.0.gamma_2" in mapped
    assert not any(".ls1.gamma" in key or ".ls2.gamma" in key for key in mapped)
    assert mapped["reg_token"].shape == (1, 2, 3)
    assert "storage_tokens" not in mapped
    # The backbone prefix itself is stripped from every emitted key.
    assert not any(key.startswith("backbone.") for key in mapped)


def test_pool_skips_register_tokens_after_cls() -> None:
    batch, channels = 2, 4
    tokens = torch.zeros((batch, 1 + NUM_REGISTER_TOKENS + 3, channels))
    tokens[:, 0] = 1.0
    tokens[:, 1 + NUM_REGISTER_TOKENS :] = 2.0
    # Register tokens carry values that would visibly poison a patch mean.
    tokens[:, 1 : 1 + NUM_REGISTER_TOKENS] = 1e6

    pooled = pool_feature_tokens(tokens)

    assert pooled.shape == (batch, 2 * channels)
    assert torch.equal(pooled[:, :channels], torch.ones((batch, channels)))
    assert torch.equal(pooled[:, channels:], 2.0 * torch.ones((batch, channels)))


def test_pool_ignores_nan_register_tokens() -> None:
    tokens = torch.zeros((1, 1 + NUM_REGISTER_TOKENS + 2, 3))
    tokens[:, 0] = 5.0
    tokens[:, 1 + NUM_REGISTER_TOKENS :] = 7.0
    tokens[:, 1 : 1 + NUM_REGISTER_TOKENS] = float("nan")

    pooled = pool_feature_tokens(tokens)

    assert torch.isfinite(pooled).all()
    assert pooled[0, :3].tolist() == [5.0, 5.0, 5.0]
    assert pooled[0, 3:].tolist() == [7.0, 7.0, 7.0]


def test_pool_rejects_tensors_without_register_headroom() -> None:
    with pytest.raises(RuntimeError, match="too short"):
        pool_feature_tokens(torch.zeros((1, 1 + NUM_REGISTER_TOKENS, 8)))


def test_projector_output_dimension_is_read_from_checkpoint() -> None:
    state = {
        "projector.0.weight": torch.zeros((10, 24)),
        "projector.0.bias": torch.zeros(10),
        "projector.2.weight": torch.zeros((7, 10)),
        "projector.2.bias": torch.zeros(7),
    }
    projector = _build_projector(state)

    assert projector[0].in_features == 24
    assert projector[0].out_features == 10
    assert projector[2].out_features == 7
    with torch.no_grad():
        assert projector(torch.zeros((3, 24))).shape == (3, 7)


def test_projector_rejects_hidden_dimension_mismatch() -> None:
    state = {
        "projector.0.weight": torch.zeros((10, 24)),
        "projector.0.bias": torch.zeros(10),
        "projector.2.weight": torch.zeros((7, 11)),
        "projector.2.bias": torch.zeros(7),
    }
    with pytest.raises(RuntimeError, match="hidden dimension"):
        _build_projector(state)


def test_remapped_keys_load_strictly_into_the_pinned_timm_model() -> None:
    """Guards the exact contract that broke when qkv.bias was dropped, not split.

    The synthetic state mirrors the real checkpoint's backbone layout (fused qkv bias,
    bias_mask, mask token, rope periods, storage tokens) using shapes the pinned
    architecture requires, so a strict load catches any missing or extra parameter.
    """
    model = timm.create_model(
        TIMM_MODEL_NAME,
        pretrained=False,
        num_classes=0,
        img_size=KALOSCOPE_IMAGE_SIZE,
        global_pool="",
    )
    expected = model.state_dict()
    dim = int(expected["cls_token"].shape[-1])
    registers = int(expected["reg_token"].shape[1])

    state: dict[str, torch.Tensor] = {
        "backbone.cls_token": torch.zeros_like(expected["cls_token"]),
        "backbone.storage_tokens": torch.zeros((1, registers, dim)),
        "backbone.mask_token": torch.zeros((1, dim)),
        "backbone.rope_embed.periods": torch.ones_like(model.rope.periods),
        "backbone.patch_embed.proj.weight": torch.zeros_like(
            expected["patch_embed.proj.weight"]
        ),
        "backbone.patch_embed.proj.bias": torch.zeros_like(expected["patch_embed.proj.bias"]),
        "backbone.norm.weight": torch.zeros_like(expected["norm.weight"]),
        "backbone.norm.bias": torch.zeros_like(expected["norm.bias"]),
    }
    for index in range(len(model.blocks)):
        source = f"backbone.blocks.{index}."
        target = f"blocks.{index}."
        state[source + "attn.qkv.weight"] = torch.zeros_like(expected[target + "attn.qkv.weight"])
        state[source + "attn.qkv.bias"] = torch.zeros(
            expected[target + "attn.qkv.weight"].shape[0]
        )
        state[source + "attn.qkv.bias_mask"] = torch.zeros(
            expected[target + "attn.qkv.weight"].shape[0]
        )
        for name in (
            "attn.proj.weight",
            "attn.proj.bias",
            "mlp.fc1.weight",
            "mlp.fc1.bias",
            "mlp.fc2.weight",
            "mlp.fc2.bias",
            "norm1.weight",
            "norm1.bias",
            "norm2.weight",
            "norm2.bias",
        ):
            state[source + name] = torch.zeros_like(expected[target + name])
        state[source + "ls1.gamma"] = torch.zeros_like(expected[target + "gamma_1"])
        state[source + "ls2.gamma"] = torch.zeros_like(expected[target + "gamma_2"])

    mapped = _remap_state_dict(state)

    assert set(mapped) == set(expected)
    model.load_state_dict(mapped, strict=True)
