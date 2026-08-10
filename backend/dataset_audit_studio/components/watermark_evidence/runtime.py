from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
import transformers
from PIL import Image
from torch import nn

from dataset_audit_studio.components.watermark_evidence.config import WatermarkEvidenceConfig
from dataset_audit_studio.core.model_assets import (
    RuntimeAssets,
    verify_runtime_asset_snapshot,
)
from dataset_audit_studio.core.torch_runtime import (
    autocast_context,
    release_torch_memory,
    resolve_torch_device,
)

WATERMARK_MODEL_ID = "watermark_siglip2"


class WatermarkEvidenceRuntime:
    def __init__(self, config: WatermarkEvidenceConfig, assets: RuntimeAssets) -> None:
        verify_runtime_asset_snapshot(assets)
        self.config = config
        self.device = resolve_torch_device(config.device, config.precision)
        root = Path(assets.get(WATERMARK_MODEL_ID).root)
        self.processor = transformers.AutoImageProcessor.from_pretrained(
            root,
            local_files_only=True,
            trust_remote_code=False,
        )
        model = transformers.AutoModelForImageClassification.from_pretrained(
            root,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        ).eval().requires_grad_(False).to(self.device)
        self.model: nn.Module | None = model
        labels = {int(index): str(label) for index, label in model.config.id2label.items()}
        matches = [
            index
            for index, label in labels.items()
            if label.casefold().replace("_", " ").strip() == "watermark"
        ]
        if len(matches) != 1:
            raise RuntimeError("Watermark model config must define exactly one Watermark label")
        self.labels = labels
        self.watermark_index = matches[0]

    def score(self, images: tuple[Image.Image, ...]) -> list[dict[str, Any]]:
        if self.processor is None or self.model is None:
            raise RuntimeError("Watermark runtime was not initialized")
        inputs = self.processor(
            images=[image.convert("RGB") for image in images],
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode(), autocast_context(self.device, self.config.precision):
            logits = self.model(**inputs).logits
            probabilities = torch.softmax(logits.float(), dim=-1).detach().cpu()
        return [
            {
                "watermark_probability": float(row[self.watermark_index].item()),
                "probabilities": {
                    self.labels[index]: float(row[index].item())
                    for index in sorted(self.labels)
                },
            }
            for row in probabilities
        ]

    def close(self) -> None:
        self.processor = None
        self.model = None
        release_torch_memory()

