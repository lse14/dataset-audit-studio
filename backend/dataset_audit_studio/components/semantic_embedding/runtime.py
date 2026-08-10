from __future__ import annotations

from pathlib import Path

import torch
import torch.nn.functional as functional
import transformers
from PIL import Image, ImageOps

from dataset_audit_studio.components.semantic_embedding.config import (
    SemanticEmbeddingConfig,
)
from dataset_audit_studio.components.semantic_embedding.contracts import (
    SemanticEmbeddingBatch,
    SemanticSample,
)
from dataset_audit_studio.core.model_assets import (
    RuntimeAssets,
    verify_runtime_asset_snapshot,
)
from dataset_audit_studio.core.torch_runtime import release_torch_memory, resolve_torch_device

SIGLIP_MODEL_ID = "siglip2_so400m_naflex"


def _image_feature_tensor(output: object) -> torch.Tensor:
    if isinstance(output, torch.Tensor):
        return output
    pooled = getattr(output, "pooler_output", None)
    if isinstance(pooled, torch.Tensor):
        return pooled
    raise RuntimeError("SigLIP2 image features do not contain a pooled tensor")


class TorchEmbeddingRuntime:
    def __init__(self, config: SemanticEmbeddingConfig, assets: RuntimeAssets) -> None:
        verify_runtime_asset_snapshot(assets)
        self.config = config
        self.assets = assets
        self.device = resolve_torch_device(config.device, "float32")
        root = Path(assets.get(SIGLIP_MODEL_ID).root)
        self.processor = transformers.AutoImageProcessor.from_pretrained(
            root,
            local_files_only=True,
            trust_remote_code=False,
        )
        self.model = transformers.AutoModel.from_pretrained(
            root,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        ).eval().requires_grad_(False).to(self.device)

    def embed(self, samples: tuple[SemanticSample, ...]) -> SemanticEmbeddingBatch:
        images = tuple(self._open_image(sample.image_path) for sample in samples)
        try:
            inputs = self.processor(images=list(images), return_tensors="pt").to(self.device)
            with torch.inference_mode():
                if not hasattr(self.model, "get_image_features"):
                    raise RuntimeError("SigLIP2 model does not expose get_image_features")
                output = self.model.get_image_features(**inputs)
                features = _image_feature_tensor(output).float()
                features = functional.normalize(features, dim=1)
            return SemanticEmbeddingBatch(
                sample_ids=tuple(sample.sample_id for sample in samples),
                embeddings=features.detach().cpu().numpy(),
            )
        finally:
            for image in images:
                image.close()

    def close(self) -> None:
        self.model = None
        self.processor = None
        release_torch_memory()

    @staticmethod
    def _open_image(path: Path) -> Image.Image:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.load()
        return image
