from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from dataset_audit_studio.export.image_conversion import encode_export_image, output_suffix
from PIL import Image


@pytest.mark.parametrize(
    ("image_format", "expected_format", "expected_suffix"),
    (
        ("jpeg", "JPEG", ".jpg"),
        ("png", "PNG", ".png"),
        ("webp", "WEBP", ".webp"),
    ),
)
def test_encode_export_image_uses_selected_format_and_transparency_rules(
    tmp_path: Path,
    image_format: str,
    expected_format: str,
    expected_suffix: str,
) -> None:
    source = tmp_path / "transparent.png"
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((1, 0), (10, 20, 30, 64))
    image.save(source, format="PNG")
    image.close()

    encoded = encode_export_image(source, image_format)

    with Image.open(BytesIO(encoded)) as exported:
        assert exported.format == expected_format
        assert output_suffix(image_format) == expected_suffix
        if image_format == "jpeg":
            assert exported.mode == "RGB"
            assert min(exported.getpixel((0, 0))) >= 245
        else:
            assert exported.mode == "RGBA"
            assert exported.getpixel((0, 0))[3] == 0
            assert exported.getpixel((1, 0))[3] == 64
