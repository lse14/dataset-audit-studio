from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Literal, cast

from PIL import Image, ImageOps

ExportImageFormat = Literal["original", "jpeg", "png", "webp"]
ImageExportFormat = Literal["jpeg", "png", "webp"]

_FORMAT_DETAILS: dict[ImageExportFormat, tuple[str, str]] = {
    "jpeg": ("JPEG", ".jpg"),
    "png": ("PNG", ".png"),
    "webp": ("WEBP", ".webp"),
}


def normalize_image_format(value: object) -> ExportImageFormat:
    if not isinstance(value, str) or value not in {"original", "jpeg", "png", "webp"}:
        raise ValueError("image format must be original, jpeg, png, or webp")
    return cast(ExportImageFormat, value)


def output_suffix(image_format: ImageExportFormat) -> str:
    return _FORMAT_DETAILS[image_format][1]


def encode_export_image(source: Path, image_format: ImageExportFormat) -> bytes:
    pillow_format, _ = _FORMAT_DETAILS[image_format]
    with Image.open(source) as source_image:
        image = ImageOps.exif_transpose(source_image)
        try:
            rgba = image.convert("RGBA")
            try:
                output = BytesIO()
                if image_format == "jpeg":
                    background = Image.new("RGBA", rgba.size, "white")
                    try:
                        background.alpha_composite(rgba)
                        background.convert("RGB").save(output, format=pillow_format, quality=95)
                    finally:
                        background.close()
                elif image_format == "webp":
                    rgba.save(output, format=pillow_format, quality=95)
                else:
                    rgba.save(output, format=pillow_format)
                return output.getvalue()
            finally:
                rgba.close()
        finally:
            image.close()
