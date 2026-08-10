from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as functional
import transformers
from PIL import Image, ImageOps
from torch import Tensor, nn
from torchvision import models, transforms
from torchvision.transforms import InterpolationMode

from dataset_audit_studio.components.artist_style.assets import (
    DINO_MODEL_ID,
    LSNET_MODEL_ID,
    VGG_MODEL_ID,
)
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.components.artist_style.contracts import (
    StyleFeatureBatch,
    StyleSample,
)
from dataset_audit_studio.components.artist_style.lsnet_runtime import load_lsnet_feature_model
from dataset_audit_studio.core.model_assets import (
    RuntimeAssets,
    verify_runtime_asset_snapshot,
)
from dataset_audit_studio.core.torch_runtime import release_torch_memory, resolve_torch_device

VGG_LAYERS = (1, 6, 11, 20)
GRAM_POOL_SIZE = 16
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def gram_matrix_batch(feature_map: Tensor) -> Tensor:
    batch, channels, height, width = feature_map.shape
    flattened = feature_map.reshape(batch, channels, height * width)
    gram = torch.bmm(flattened, flattened.transpose(1, 2))
    return gram / max(float(channels * height * width), 1e-12)


def extract_vgg19_gram_embeddings(batch: Tensor, feature_model: nn.Module) -> Tensor:
    activations: list[Tensor] = []
    hidden = batch
    for index, layer in enumerate(feature_model):
        hidden = layer(hidden)
        if index in VGG_LAYERS:
            gram = gram_matrix_batch(hidden).unsqueeze(1)
            pooled = functional.adaptive_avg_pool2d(gram, (GRAM_POOL_SIZE, GRAM_POOL_SIZE))
            activations.append(functional.normalize(pooled.flatten(start_dim=1), dim=1))
        if index >= VGG_LAYERS[-1]:
            break
    if len(activations) != len(VGG_LAYERS):
        raise RuntimeError("VGG19 style feature extraction missed configured layers")
    return functional.normalize(torch.cat(activations, dim=1), dim=1)


class TorchStyleRuntime:
    def __init__(self, config: StyleConfig, assets: RuntimeAssets) -> None:
        verify_runtime_asset_snapshot(assets)
        self.config = config
        self.assets = assets
        self.device = resolve_torch_device(config.device, "float32")
        if config.lsnet_weight > 0.0:
            self.lsnet_transform, self.lsnet = self._load_lsnet()
        else:
            self.lsnet_transform, self.lsnet = None, None
        self.vgg = self._load_vgg() if config.gram_weight > 0.0 else None
        if config.dino_weight > 0.0:
            self.dino_processor, self.dino = self._load_dino()
            mean = tuple(getattr(self.dino_processor, "image_mean", IMAGENET_MEAN))
            std = tuple(getattr(self.dino_processor, "image_std", IMAGENET_STD))
        else:
            self.dino_processor, self.dino = None, None
            mean, std = IMAGENET_MEAN, IMAGENET_STD
        self.dino_mean = torch.tensor(mean, dtype=torch.float32).view(3, 1, 1)
        self.dino_std = torch.tensor(std, dtype=torch.float32).view(3, 1, 1)
        self.vgg_transform = (
            transforms.Compose(
                (
                    transforms.Resize(
                        config.image_size,
                        interpolation=InterpolationMode.BICUBIC,
                        antialias=True,
                    ),
                    transforms.CenterCrop(config.image_size),
                    transforms.ToTensor(),
                    transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
                )
            )
            if self.vgg is not None
            else None
        )

    def extract(self, samples: tuple[StyleSample, ...]) -> StyleFeatureBatch:
        images = tuple(self._open_image(sample.image_path) for sample in samples)
        try:
            with torch.inference_mode():
                if self.lsnet is None or self.lsnet_transform is None:
                    # Keep the persisted feature contract stable without loading
                    # a model whose configured contribution is zero.
                    lsnet = torch.ones((len(images), 1), device=self.device)
                else:
                    lsnet_batch = torch.stack(
                        [self.lsnet_transform(image) for image in images]
                    ).to(self.device)
                    lsnet = functional.normalize(
                        self.lsnet(lsnet_batch, return_features=True).float(), dim=1
                    )
                if self.vgg is None or self.vgg_transform is None:
                    gram = torch.ones((len(images), 1), device=self.device)
                else:
                    vgg_batch = torch.stack(
                        [self.vgg_transform(image) for image in images]
                    ).to(self.device)
                    gram = extract_vgg19_gram_embeddings(vgg_batch, self.vgg).float()
                if self.dino is None:
                    dino = torch.ones((len(images), 1), device=self.device)
                else:
                    dino_batch = torch.stack(
                        [self._dino_tensor(image) for image in images]
                    ).to(self.device)
                    outputs = self.dino(pixel_values=dino_batch)
                    pooled = getattr(outputs, "pooler_output", None)
                    if pooled is None:
                        pooled = outputs.last_hidden_state[:, 0]
                    dino = functional.normalize(pooled.float(), dim=1)
            colors = np.stack([self._color_histogram(image) for image in images])
            return StyleFeatureBatch(
                sample_ids=tuple(sample.sample_id for sample in samples),
                lsnet=lsnet.detach().cpu().numpy(),
                gram=gram.detach().cpu().numpy(),
                dino=dino.detach().cpu().numpy(),
                color_histogram=colors,
            )
        finally:
            for image in images:
                image.close()

    def close(self) -> None:
        self.lsnet = None
        self.vgg = None
        self.dino = None
        self.dino_processor = None
        release_torch_memory()

    def _load_lsnet(self):
        asset = self.assets.get(LSNET_MODEL_ID)
        return load_lsnet_feature_model(
            asset.file_path("448-90.13/best_checkpoint.pth"),
            self.device,
        )

    def _load_vgg(self) -> nn.Module:
        asset = self.assets.get(VGG_MODEL_ID)
        if len(asset.files) != 1:
            raise RuntimeError("VGG19 registry asset must contain exactly one weight file")
        model = models.vgg19(weights=None)
        state = torch.load(
            asset.file_path(asset.files[0].path),
            map_location="cpu",
            weights_only=True,
        )
        model.load_state_dict(state, strict=True)
        return model.features.eval().requires_grad_(False).to(self.device)

    def _load_dino(self):
        root = Path(self.assets.get(DINO_MODEL_ID).root)
        processor = transformers.AutoImageProcessor.from_pretrained(
            root,
            local_files_only=True,
            trust_remote_code=False,
        )
        model = transformers.AutoModel.from_pretrained(
            root,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        ).eval().requires_grad_(False).to(self.device)
        return processor, model

    def _dino_tensor(self, image: Image.Image) -> Tensor:
        width, height = image.size
        side = max(width, height)
        canvas = Image.new("RGB", (side, side), (0, 0, 0))
        canvas.paste(image, ((side - width) // 2, (side - height) // 2))
        resized = canvas.resize(
            (self.config.image_size, self.config.image_size),
            Image.Resampling.BICUBIC,
        )
        tensor = transforms.functional.to_tensor(resized)
        return (tensor - self.dino_mean) / self.dino_std

    @staticmethod
    def _open_image(path: Path) -> Image.Image:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.load()
        return image

    @staticmethod
    def _color_histogram(image: Image.Image) -> np.ndarray:
        pixels = np.asarray(
            image.resize((128, 128), Image.Resampling.BICUBIC),
            dtype=np.uint8,
        ).reshape(-1, 3)
        bins = np.arange(0, 257, 32)
        parts = [np.histogram(pixels[:, channel], bins=bins)[0] for channel in range(3)]
        histogram = np.concatenate(parts).astype(np.float32)
        return histogram / max(float(histogram.sum()), 1.0)
