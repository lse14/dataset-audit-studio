from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import open_clip
import torch
import torch.nn.functional as functional
from PIL import Image
from torchvision import transforms

from dataset_audit_studio.components.clip_features.config import ClipFeatureConfig
from dataset_audit_studio.core.feature_batch import FeatureBatch
from dataset_audit_studio.core.model_assets import (
    RuntimeAssets,
    verify_runtime_asset_snapshot,
)
from dataset_audit_studio.core.torch_runtime import release_torch_memory, resolve_torch_device

CLIP_MODEL_ID = "openai_clip_vit_l14"
AESTHETIC_FEATURE_CAPABILITY = "embedding.clip_l14.aesthetic.v1"
UFD_FEATURE_CAPABILITY = "embedding.clip_l14.ufd.v1"
CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)


class _PinnedClipBootstrapWarningFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith(
            "No pretrained weights loaded for model 'ViT-L-14'."
        )


@contextmanager
def suppress_pinned_clip_bootstrap_warning():
    warning_filter = _PinnedClipBootstrapWarningFilter()
    root_logger = logging.getLogger()
    root_logger.addFilter(warning_filter)
    try:
        yield
    finally:
        root_logger.removeFilter(warning_filter)


def build_ufd_transform():
    return transforms.Compose(
        (
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=CLIP_MEAN, std=CLIP_STD),
        )
    )


def load_pinned_clip_state_dict(path: Path) -> dict[str, torch.Tensor]:
    scripted = torch.jit.load(str(path), map_location="cpu")
    state_dict = dict(scripted.state_dict())
    for key in ("input_resolution", "context_length", "vocab_size"):
        state_dict.pop(key, None)
    if not state_dict or not all(isinstance(value, torch.Tensor) for value in state_dict.values()):
        raise RuntimeError("Pinned CLIP TorchScript archive has an invalid state dict")
    return state_dict


class ClipFeatureRuntime:
    def __init__(self, config: ClipFeatureConfig, assets: RuntimeAssets) -> None:
        verify_runtime_asset_snapshot(assets)
        self.config = config
        self.device = resolve_torch_device(config.device, config.precision)
        asset = assets.get(CLIP_MODEL_ID)
        if asset.loader != "openai_clip_vit_l14_v1" or asset.is_custom:
            raise RuntimeError("CLIP requires the pinned registry TorchScript asset")
        with suppress_pinned_clip_bootstrap_warning():
            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-L-14",
                pretrained=None,
                device="cpu",
                force_quick_gelu=True,
            )
        model.load_state_dict(
            load_pinned_clip_state_dict(asset.file_path("ViT-L-14.pt")),
            strict=True,
        )
        self.model = model.eval().requires_grad_(False).to(self.device, dtype=torch.float32)
        self.aesthetic_preprocess = preprocess
        self.ufd_preprocess = build_ufd_transform()

    def extract(
        self,
        images: tuple[Image.Image, ...],
        sample_ids: tuple[str, ...],
        capabilities: tuple[str, ...],
    ) -> FeatureBatch:
        if len(images) != len(sample_ids):
            raise ValueError("CLIP images and sample ids must have equal lengths")
        requested = set(capabilities)
        supported = {AESTHETIC_FEATURE_CAPABILITY, UFD_FEATURE_CAPABILITY}
        unknown = requested - supported
        if unknown:
            raise ValueError(f"Unsupported CLIP feature capabilities: {sorted(unknown)}")
        features = {}
        with torch.inference_mode():
            if AESTHETIC_FEATURE_CAPABILITY in requested:
                batch = torch.stack(
                    [self.aesthetic_preprocess(image.convert("RGB")) for image in images]
                ).to(self.device)
                values = functional.normalize(self.model.encode_image(batch).float(), dim=-1)
                features[AESTHETIC_FEATURE_CAPABILITY] = values.detach().cpu().numpy()
            if UFD_FEATURE_CAPABILITY in requested:
                batch = torch.stack(
                    [self.ufd_preprocess(image.convert("RGB")) for image in images]
                ).to(self.device)
                values = self.model.encode_image(batch).float()
                features[UFD_FEATURE_CAPABILITY] = values.detach().cpu().numpy()
        return FeatureBatch.create(sample_ids, features)

    def close(self) -> None:
        self.model = None
        self.aesthetic_preprocess = None
        self.ufd_preprocess = None
        release_torch_memory()
