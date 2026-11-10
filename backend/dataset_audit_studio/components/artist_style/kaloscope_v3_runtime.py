from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import timm
import torch
from PIL import Image
from safetensors import safe_open
from torch import Tensor, nn
from torchvision import transforms
from torchvision.transforms import InterpolationMode

TIMM_MODEL_NAME = "vit_base_patch16_dinov3_qkvb"
# Kaloscope v3 is trained at 512. This is deliberately independent of
# StyleConfig.image_size (224), which historical task configs pin for VGG/DINO.
KALOSCOPE_IMAGE_SIZE = 512
# forward_features returns 1 CLS + 4 register tokens + patch tokens. The register
# tokens are learned scratch space, not image content, so they must be skipped
# before averaging patches: including them drags cosine similarity down to noise.
NUM_REGISTER_TOKENS = 4
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

BACKBONE_PREFIX = "backbone."
PROJECTOR_PREFIX = "projector."
PROJECTOR_KEYS = (
    "projector.0.weight",
    "projector.0.bias",
    "projector.2.weight",
    "projector.2.bias",
)
DROPPED_BACKBONE_KEYS = frozenset({"mask_token", "rope_embed.periods"})
ROPE_PERIODS_KEY = "backbone.rope_embed.periods"
QKV_BIAS_SUFFIX = "attn.qkv.bias"
QKV_BIAS_MASK_SUFFIX = "attn.qkv.bias_mask"


class KaloscopeV3Encoder(nn.Module):
    """DINOv3 ViT-B/16 backbone plus the Kaloscope v3 projection head."""

    def __init__(
        self,
        backbone: nn.Module,
        projector: nn.Module,
        *,
        expected_tokens: int,
    ) -> None:
        super().__init__()
        self.backbone = backbone
        self.projector = projector
        self.expected_tokens = expected_tokens

    def forward(self, batch: Tensor, return_features: bool = False) -> Tensor:
        tokens = self.backbone.forward_features(batch)
        if not isinstance(tokens, Tensor) or tokens.ndim != 3:
            raise RuntimeError("Kaloscope v3 backbone returned an unexpected feature payload")
        if tokens.shape[1] != self.expected_tokens:
            raise RuntimeError(
                "Kaloscope v3 backbone returned "
                f"{tokens.shape[1]} tokens, expected {self.expected_tokens}"
            )
        # Unnormalized projection features; the caller applies L2 normalization so
        # the persisted contract matches the previous encoder.
        return self.projector(pool_feature_tokens(tokens).float())


def pool_feature_tokens(tokens: Tensor) -> Tensor:
    """Pool CLS plus mean patch tokens, skipping the trailing register block."""
    if tokens.ndim != 3:
        raise RuntimeError("Kaloscope v3 token tensor must be [batch, tokens, channels]")
    first_patch = 1 + NUM_REGISTER_TOKENS
    if tokens.shape[1] <= first_patch:
        raise RuntimeError("Kaloscope v3 token tensor is too short to hold register tokens")
    cls_token = tokens[:, 0]
    patch_tokens = tokens[:, first_patch:]
    return torch.cat([cls_token, patch_tokens.mean(dim=1)], dim=-1)


def load_kaloscope_v3_feature_model(
    checkpoint_path: Path,
    device: torch.device,
) -> tuple[Callable[[Image.Image], Tensor], nn.Module]:
    backbone_state, projector_state, rope_periods = _read_checkpoint(checkpoint_path)
    backbone = timm.create_model(
        TIMM_MODEL_NAME,
        pretrained=False,
        num_classes=0,
        img_size=KALOSCOPE_IMAGE_SIZE,
        global_pool="",
    )
    try:
        backbone.load_state_dict(backbone_state, strict=True)
    except RuntimeError as error:
        raise RuntimeError(
            "Kaloscope v3 checkpoint is incompatible with the pinned timm architecture "
            f"{TIMM_MODEL_NAME}: {error}"
        ) from error
    # timm recomputes RoPE periods on construction and never stores them in the
    # state dict, so the checkpoint values are copied in to reproduce training numerics.
    backbone.rope.periods.copy_(rope_periods.to(backbone.rope.periods.dtype))
    projector = _build_projector(projector_state)
    pooled_dim = 2 * int(backbone.patch_embed.proj.weight.shape[0])
    projector_input = int(projector_state["projector.0.weight"].shape[1])
    if pooled_dim != projector_input:
        raise RuntimeError(
            "Kaloscope v3 projector expects "
            f"{projector_input} pooled features but the backbone produces {pooled_dim}"
        )
    model = KaloscopeV3Encoder(
        backbone,
        projector,
        expected_tokens=int(backbone.patch_embed.num_patches) + 1 + NUM_REGISTER_TOKENS,
    )
    transform = transforms.Compose(
        (
            transforms.Resize(
                KALOSCOPE_IMAGE_SIZE,
                interpolation=InterpolationMode.BICUBIC,
                antialias=True,
            ),
            transforms.CenterCrop(KALOSCOPE_IMAGE_SIZE),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        )
    )
    return transform, model.to(device).eval().requires_grad_(False)


def _read_checkpoint(
    checkpoint_path: Path,
) -> tuple[dict[str, Tensor], dict[str, Tensor], Tensor]:
    with safe_open(str(checkpoint_path), framework="pt", device="cpu") as handle:
        available = tuple(handle.keys())
        backbone_keys = [key for key in available if key.startswith(BACKBONE_PREFIX)]
        projector_keys = [key for key in available if key.startswith(PROJECTOR_PREFIX)]
        if not backbone_keys:
            raise RuntimeError(
                f"Kaloscope v3 checkpoint {checkpoint_path.name} has no backbone tensors"
            )
        raw = {key: handle.get_tensor(key) for key in backbone_keys + projector_keys}
    rope_periods = raw.pop(ROPE_PERIODS_KEY, None)
    if rope_periods is None:
        raise RuntimeError(
            f"Kaloscope v3 checkpoint {checkpoint_path.name} is missing {ROPE_PERIODS_KEY}"
        )
    backbone_state = _remap_state_dict(raw)
    projector_state = {
        key: raw[key] for key in PROJECTOR_KEYS if key in raw
    }
    missing = [key for key in PROJECTOR_KEYS if key not in projector_state]
    if missing:
        raise RuntimeError(
            "Kaloscope v3 checkpoint is missing projector weights: " + ", ".join(missing)
        )
    return backbone_state, projector_state, rope_periods


def _remap_state_dict(state_dict: Mapping[str, Tensor]) -> dict[str, Tensor]:
    """Map Kaloscope v3 backbone keys onto the pinned timm DINOv3 layout."""
    mapped: dict[str, Tensor] = {}
    for key, value in state_dict.items():
        if not key.startswith(BACKBONE_PREFIX):
            continue
        local = key.removeprefix(BACKBONE_PREFIX)
        if local in DROPPED_BACKBONE_KEYS or local.endswith(QKV_BIAS_MASK_SUFFIX):
            continue
        if local.endswith(QKV_BIAS_SUFFIX):
            # timm keeps separate q_bias/v_bias parameters for the qk-normalized
            # variant. The checkpoint values are zero, but the parameters must exist.
            block = local[: -len(QKV_BIAS_SUFFIX)]
            query_bias, _key_bias, value_bias = value.chunk(3, dim=0)
            mapped[block + "attn.q_bias"] = query_bias
            mapped[block + "attn.v_bias"] = value_bias
            continue
        local = local.replace(".ls1.gamma", ".gamma_1").replace(".ls2.gamma", ".gamma_2")
        if local == "storage_tokens":
            local = "reg_token"
        mapped[local] = value
    return mapped


def _build_projector(state_dict: Mapping[str, Tensor]) -> nn.Sequential:
    first = state_dict["projector.0.weight"]
    last = state_dict["projector.2.weight"]
    hidden_dim = int(first.shape[0])
    input_dim = int(first.shape[1])
    output_dim = int(last.shape[0])
    if int(last.shape[1]) != hidden_dim:
        raise RuntimeError(
            "Kaloscope v3 projector hidden dimension mismatch: "
            f"{int(last.shape[1])} != {hidden_dim}"
        )
    projector = nn.Sequential(
        nn.Linear(input_dim, hidden_dim),
        nn.GELU(),
        nn.Linear(hidden_dim, output_dim),
    )
    with torch.no_grad():
        projector[0].weight.copy_(state_dict["projector.0.weight"])
        projector[0].bias.copy_(state_dict["projector.0.bias"])
        projector[2].weight.copy_(state_dict["projector.2.weight"])
        projector[2].bias.copy_(state_dict["projector.2.bias"])
    return projector
