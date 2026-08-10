from __future__ import annotations

from io import BytesIO
from math import ceil
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as functional
from PIL import Image, ImageCms, ImageOps
from safetensors import safe_open
from torch import Tensor, nn

JTP3_ARCHITECTURE = "naflexvit_so400m_patch16_siglip+rr_hydra"
JTP3_CLASS_COUNT = 7504
JTP3_PATCH_SIZE = 16
JTP3_PATCH_DIM = JTP3_PATCH_SIZE * JTP3_PATCH_SIZE * 3


def image_size_for_sequence(
    image_height: int,
    image_width: int,
    *,
    patch_size: int = JTP3_PATCH_SIZE,
    max_sequence: int = 1024,
    max_ratio: float = 1.0,
    epsilon: float = 1e-5,
) -> tuple[int, int]:
    if image_height <= 0 or image_width <= 0:
        raise ValueError("Image dimensions must be positive")
    if max_ratio < 1.0 or epsilon * 2 >= max_ratio:
        raise ValueError("Invalid JTP-3 resize bounds")
    max_rows = max(int((image_height * max_ratio) // patch_size), 1)
    max_columns = max(int((image_width * max_ratio) // patch_size), 1)
    if max_rows * max_columns <= max_sequence:
        return max_rows * patch_size, max_columns * patch_size

    def patch_grid(ratio: float) -> tuple[int, int]:
        return (
            min(int(ceil((image_height * ratio) / patch_size)), max_rows),
            min(int(ceil((image_width * ratio) / patch_size)), max_columns),
        )

    rows, columns = patch_grid(epsilon)
    if rows * columns > max_sequence:
        raise ValueError("Image aspect ratio cannot fit the JTP-3 sequence limit")
    ratio = epsilon
    upper = max_ratio
    while upper - ratio >= epsilon:
        midpoint = (ratio + upper) / 2.0
        candidate_rows, candidate_columns = patch_grid(midpoint)
        sequence = candidate_rows * candidate_columns
        if sequence > max_sequence:
            upper = midpoint
            continue
        ratio = midpoint
        rows, columns = candidate_rows, candidate_columns
        if sequence == max_sequence:
            break
    return rows * patch_size, columns * patch_size


def prepare_jtp3_image(image: Image.Image, *, max_sequence: int) -> Image.Image:
    prepared = ImageOps.exif_transpose(image)
    prepared.load()
    icc_profile = prepared.info.get("icc_profile")
    if icc_profile:
        try:
            source_profile = ImageCms.ImageCmsProfile(BytesIO(icc_profile))
            prepared = ImageCms.profileToProfile(
                prepared,
                source_profile,
                ImageCms.createProfile("sRGB"),
                outputMode="RGBA" if prepared.has_transparency_data else "RGB",
                renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
            )
        except (OSError, TypeError, ValueError):
            pass
    if prepared.has_transparency_data:
        rgba = prepared.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        prepared = Image.alpha_composite(background, rgba).convert("RGB")
    else:
        prepared = prepared.convert("RGB")
    target_height, target_width = image_size_for_sequence(
        prepared.height,
        prepared.width,
        max_sequence=max_sequence,
    )
    if prepared.size != (target_width, target_height):
        prepared = prepared.resize(
            (target_width, target_height),
            Image.Resampling.LANCZOS,
            reducing_gap=3.0,
        )
    return prepared


def patchify_jtp3_images(
    images: tuple[Image.Image, ...],
    *,
    max_sequence: int,
) -> tuple[Tensor, Tensor, Tensor]:
    patches = torch.zeros(
        (len(images), max_sequence, JTP3_PATCH_DIM), dtype=torch.float32
    )
    coordinates = torch.zeros((len(images), max_sequence, 2), dtype=torch.int32)
    valid = torch.zeros((len(images), max_sequence), dtype=torch.bool)
    for index, source in enumerate(images):
        image = prepare_jtp3_image(source, max_sequence=max_sequence)
        pixels = np.asarray(image, dtype=np.uint8).copy()
        rows = image.height // JTP3_PATCH_SIZE
        columns = image.width // JTP3_PATCH_SIZE
        count = rows * columns
        if count > max_sequence:
            raise RuntimeError("JTP-3 preprocessor exceeded its sequence limit")
        patch_array = (
            pixels.reshape(
                rows,
                JTP3_PATCH_SIZE,
                columns,
                JTP3_PATCH_SIZE,
                3,
            )
            .transpose(0, 2, 1, 3, 4)
            .reshape(count, JTP3_PATCH_DIM)
        )
        row_grid, column_grid = np.meshgrid(
            np.arange(rows, dtype=np.int32),
            np.arange(columns, dtype=np.int32),
            indexing="ij",
        )
        coordinate_array = np.stack((row_grid, column_grid), axis=-1).reshape(count, 2)
        patches[index, :count].copy_(torch.from_numpy(patch_array).float())
        coordinates[index, :count].copy_(torch.from_numpy(coordinate_array))
        valid[index, :count] = True
    patches.div_(127.5).sub_(1.0)
    return patches, coordinates, valid


class _NaFlexEmbeddings(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.pos_embed = nn.Parameter(torch.empty(1, 16, 16, 1152))
        self.proj = nn.Linear(768, 1152)

    def forward(self, patches: Tensor, coordinates: Tensor, valid: Tensor) -> Tensor:
        patches = self.proj(patches)
        positional = self.pos_embed.permute(0, 3, 1, 2)
        for index in range(patches.shape[0]):
            count = int(valid[index].sum().item())
            grid = coordinates[index, :count].amax(dim=0) + 1
            rows, columns = int(grid[0].item()), int(grid[1].item())
            if (rows, columns) == (16, 16):
                embedding = positional.permute(0, 2, 3, 1).reshape(256, 1152)
            else:
                embedding = functional.interpolate(
                    positional,
                    size=(rows, columns),
                    mode="bilinear",
                    align_corners=False,
                    antialias=True,
                ).permute(0, 2, 3, 1).reshape(rows * columns, 1152)
            patches[index, :count] = patches[index, :count] + embedding
        return patches


class _NaFlexAttention(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.qkv = nn.Linear(1152, 3456)
        self.proj = nn.Linear(1152, 1152)

    def forward(self, hidden: Tensor, mask: Tensor) -> Tensor:
        batch, sequence, _ = hidden.shape
        qkv = self.qkv(hidden).reshape(batch, sequence, 3, 16, 72)
        query, key, value = qkv.permute(2, 0, 3, 1, 4).unbind(0)
        attended = functional.scaled_dot_product_attention(
            query, key, value, attn_mask=mask
        )
        return self.proj(attended.transpose(1, 2).reshape(batch, sequence, 1152))


class _NaFlexMlp(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.fc1 = nn.Linear(1152, 4304)
        self.fc2 = nn.Linear(4304, 1152)

    def forward(self, hidden: Tensor) -> Tensor:
        return self.fc2(functional.gelu(self.fc1(hidden), approximate="tanh"))


class _NaFlexBlock(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.attn = _NaFlexAttention()
        self.mlp = _NaFlexMlp()
        self.norm1 = nn.LayerNorm(1152)
        self.norm2 = nn.LayerNorm(1152)

    def forward(self, hidden: Tensor, mask: Tensor) -> Tensor:
        hidden = hidden + self.attn(self.norm1(hidden), mask)
        return hidden + self.mlp(self.norm2(hidden))


class _ClassProjection(nn.Module):
    def __init__(self, classes: int, features: int) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.empty(classes, features, 2))

    def forward(self, hidden: Tensor) -> Tensor:
        return torch.matmul(hidden.unsqueeze(-2), self.weight).squeeze(-2)


class _HydraPool(nn.Module):
    def __init__(self, classes: int) -> None:
        super().__init__()
        self.q = nn.Parameter(torch.empty(32, classes, 64))
        self.kv = nn.Linear(1152, 4096, bias=False)
        self.qk_norm = nn.RMSNorm(64, eps=1e-5, elementwise_affine=False)
        self.ff_norm = nn.LayerNorm(2048)
        self.ff_in = nn.Linear(2048, 12288, bias=False)
        self.ff_out = nn.Linear(6144, 2048, bias=False)
        self.out_proj = _ClassProjection(classes, 2048)

    def forward(self, hidden: Tensor, mask: Tensor) -> Tensor:
        batch, sequence, _ = hidden.shape
        query = self.q.expand(batch, -1, -1, -1)
        key_value = self.kv(hidden).reshape(batch, sequence, 2, 32, 64)
        key, value = key_value.permute(2, 0, 3, 1, 4).unbind(0)
        key = self.qk_norm(key)
        attended = functional.scaled_dot_product_attention(
            query, key, value, attn_mask=mask
        )
        attended = attended.transpose(1, 2).reshape(batch, -1, 2048)
        normalized = self.ff_norm(attended)
        gate, values = self.ff_in(normalized).chunk(2, dim=-1)
        attended = attended + self.ff_out(functional.silu(gate) * values)
        gate, values = self.out_proj(attended).unbind(-1)
        return functional.silu(gate) * values


class JTP3Model(nn.Module):
    def __init__(self, classes: int = JTP3_CLASS_COUNT) -> None:
        super().__init__()
        self.embeds = _NaFlexEmbeddings()
        self.blocks = nn.ModuleList(_NaFlexBlock() for _ in range(27))
        self.norm = nn.LayerNorm(1152)
        self.attn_pool = _HydraPool(classes)

    def forward(self, patches: Tensor, coordinates: Tensor, valid: Tensor) -> Tensor:
        mask = valid[:, None, None, :]
        hidden = self.embeds(patches, coordinates, valid)
        for block in self.blocks:
            hidden = block(hidden, mask)
        return self.attn_pool(self.norm(hidden), mask)


def load_jtp3_model(path: Path, *, device: torch.device) -> JTP3Model:
    with safe_open(str(path), framework="pt", device="cpu") as tensors:
        metadata = dict(tensors.metadata() or {})
        architecture = metadata.get("modelspec.architecture")
        if architecture != JTP3_ARCHITECTURE:
            raise RuntimeError(f"Unsupported JTP-3 architecture: {architecture}")
        labels = str(metadata.get("classifier.labels", "")).splitlines()
        if len(labels) != JTP3_CLASS_COUNT:
            raise RuntimeError(
                f"JTP-3 classifier has {len(labels)} labels, expected {JTP3_CLASS_COUNT}"
            )
        tensor_keys = tensors.keys()
        state = {key: tensors.get_tensor(key).float() for key in tensor_keys}
    model = JTP3Model(len(labels))
    model.load_state_dict(state, strict=True)
    del state
    return model.eval().requires_grad_(False).to(device=device, dtype=torch.float32)
