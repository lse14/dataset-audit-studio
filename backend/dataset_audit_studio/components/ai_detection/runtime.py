from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import torch
from PIL import Image
from torch import nn
from torchvision import transforms

from dataset_audit_studio.components.ai_detection.config import (
    COMMUNITY_FORENSICS_MODEL_ID,
    UFD_MODEL_ID,
    AIDetectionConfig,
)
from dataset_audit_studio.core.model_assets import (
    RuntimeAssets,
    verify_runtime_asset_snapshot,
)
from dataset_audit_studio.core.torch_runtime import (
    autocast_context,
    copy_float_features_to_tensor,
    release_torch_memory,
    resolve_torch_device,
)

# Kept for the benchmark adapter's existing UFD contract.
AI_MODEL_ID = UFD_MODEL_ID


def build_community_forensics_transform():
    return transforms.Compose(
        (
            transforms.Resize(440),
            transforms.CenterCrop(384),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=(0.485, 0.456, 0.406),
                std=(0.229, 0.224, 0.225),
            ),
        )
    )


class CommunityForensicsClassifier(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        import timm

        self.vit = timm.create_model(
            "vit_small_patch16_384.augreg_in21k_ft_in1k",
            pretrained=False,
        )
        self.vit.head = nn.Linear(384, 1)

    def forward(self, pixels: torch.Tensor) -> torch.Tensor:
        return self.vit(pixels)


class AIDetectionRuntime:
    def __init__(self, config: AIDetectionConfig, assets: RuntimeAssets) -> None:
        verify_runtime_asset_snapshot(assets)
        self.config = config
        self.device = resolve_torch_device(config.device, config.precision)
        self.head: nn.Module | None = None
        self.community_model: nn.Module | None = None
        self.community_preprocess = None
        if config.model_id == UFD_MODEL_ID:
            head_path = assets.get(UFD_MODEL_ID).file_path("fc_weights.pth")
            state = torch.load(head_path, map_location="cpu", weights_only=True)
            head = nn.Linear(768, 1)
            head.load_state_dict(state, strict=True)
            self.head = head.eval().requires_grad_(False).to(
                self.device, dtype=torch.float32
            )
        else:
            asset = assets.get(COMMUNITY_FORENSICS_MODEL_ID)
            if asset.loader != "community_forensics_vit_small_384_v1" or asset.is_custom:
                raise RuntimeError("Community Forensics requires the pinned registry asset")
            from safetensors.torch import load_file

            model = CommunityForensicsClassifier()
            state = load_file(str(asset.file_path("model.safetensors")), device="cpu")
            model.load_state_dict(state, strict=True)
            self.community_model = model.eval().requires_grad_(False).to(self.device)
            self.community_preprocess = build_community_forensics_transform()

    def score(
        self,
        inputs: np.ndarray | Sequence[Image.Image],
    ) -> list[dict[str, Any]]:
        if self.config.model_id == UFD_MODEL_ID:
            return self._score_ufd(inputs)
        return self._score_community(inputs)

    def _score_ufd(
        self,
        inputs: np.ndarray | Sequence[Image.Image],
    ) -> list[dict[str, Any]]:
        if self.head is None:
            raise RuntimeError("UFD detector runtime was not initialized")
        if not isinstance(inputs, np.ndarray):
            raise ValueError("UFD requires CLIP feature inputs")
        features = copy_float_features_to_tensor(inputs, device=self.device)
        if features.ndim != 2 or features.shape[1] != 768:
            raise ValueError("AI CLIP feature shape is invalid")
        with torch.inference_mode():
            probabilities = torch.sigmoid(self.head(features).flatten())
        return [
            {"probability": float(value)}
            for value in probabilities.detach().cpu().tolist()
        ]

    def _score_community(
        self,
        inputs: np.ndarray | Sequence[Image.Image],
    ) -> list[dict[str, Any]]:
        if self.community_model is None or self.community_preprocess is None:
            raise RuntimeError("Community Forensics runtime was not initialized")
        if isinstance(inputs, np.ndarray):
            raise ValueError("Community Forensics requires decoded image inputs")
        pixels = torch.stack(
            [self.community_preprocess(image.convert("RGB")) for image in inputs]
        ).to(self.device)
        with torch.inference_mode(), autocast_context(self.device, self.config.precision):
            logits = self.community_model(pixels)
        if logits.ndim != 2 or logits.shape[1] != 1:
            raise ValueError("Community Forensics output must contain one single logit per image")
        probabilities = torch.sigmoid(logits[:, 0])
        return [
            {"probability": float(value)}
            for value in probabilities.detach().cpu().tolist()
        ]

    def close(self) -> None:
        self.head = None
        self.community_model = None
        self.community_preprocess = None
        release_torch_memory()
