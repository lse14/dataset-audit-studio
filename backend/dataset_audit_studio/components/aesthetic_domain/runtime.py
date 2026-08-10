from __future__ import annotations

from typing import Any

import numpy as np
import torch
from PIL import Image

from dataset_audit_studio.components.aesthetic_domain.config import AestheticDomainConfig
from dataset_audit_studio.components.aesthetic_domain.fusion import (
    FusionHead,
    WaifuV3Head,
    load_fusion_head,
    load_waifu_head,
)
from dataset_audit_studio.components.aesthetic_domain.jtp3 import (
    JTP3Model,
    load_jtp3_model,
    patchify_jtp3_images,
)
from dataset_audit_studio.core.model_assets import (
    RuntimeAssets,
    verify_runtime_asset_snapshot,
)
from dataset_audit_studio.core.torch_runtime import (
    copy_float_features_to_tensor,
    release_torch_memory,
    resolve_torch_device,
)

JTP3_MODEL_ID = "jtp3_hydra"
WAIFU_MODEL_ID = "waifu_scorer_v3"


class AestheticDomainRuntime:
    def __init__(self, config: AestheticDomainConfig, assets: RuntimeAssets) -> None:
        verify_runtime_asset_snapshot(assets)
        self.config = config
        self.device = resolve_torch_device(config.device, config.precision)
        jtp_path = assets.get(JTP3_MODEL_ID).file_path("models/jtp-3-hydra.safetensors")
        waifu_path = assets.get(WAIFU_MODEL_ID).file_path("model.safetensors")
        aesthetic_asset = assets.get(config.model_id)
        aesthetic_name = (
            "model.safetensors" if aesthetic_asset.is_custom else "5kdataset.safetensors"
        )
        self.jtp3: JTP3Model | None = load_jtp3_model(jtp_path, device=self.device)
        self.waifu: WaifuV3Head | None = load_waifu_head(waifu_path, device=self.device)
        self.fusion: FusionHead | None = load_fusion_head(
            aesthetic_asset.file_path(aesthetic_name),
            device=self.device,
        )

    def score(
        self,
        images: tuple[Image.Image, ...],
        clip_features: np.ndarray,
    ) -> list[dict[str, Any]]:
        if self.jtp3 is None or self.waifu is None or self.fusion is None:
            raise RuntimeError("Aesthetic runtime was not initialized")
        features = copy_float_features_to_tensor(clip_features, device=self.device)
        if features.shape != (len(images), 768):
            raise ValueError("Aesthetic CLIP feature shape is invalid")
        patches, coordinates, valid = patchify_jtp3_images(
            images,
            max_sequence=self.config.jtp_max_sequence,
        )
        with torch.inference_mode():
            jtp_features = self.jtp3(
                patches.to(self.device),
                coordinates.to(self.device),
                valid.to(self.device),
            ).float()
            waifu_score = self.waifu(features).reshape(-1, 1)
            fused = torch.cat((jtp_features, features, waifu_score), dim=-1)
            if fused.shape[-1] != 8273:
                raise RuntimeError(f"Aesthetic fusion feature dimension is {fused.shape[-1]}")
            aesthetic, domain_logit = self.fusion(fused)
            domain_probability = (
                torch.sigmoid(domain_logit) if domain_logit is not None else None
            )
        scores = aesthetic.detach().cpu().tolist()
        domains = (
            domain_probability.detach().cpu().tolist()
            if domain_probability is not None
            else [None] * len(scores)
        )
        return [
            {
                "aesthetic": float(score),
                "in_domain_prob": float(domain) if domain is not None else None,
                "in_domain_supported": domain is not None,
            }
            for score, domain in zip(scores, domains, strict=True)
        ]

    def close(self) -> None:
        self.jtp3 = None
        self.waifu = None
        self.fusion = None
        release_torch_memory()
