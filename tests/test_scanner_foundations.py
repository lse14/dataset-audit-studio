from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from dataset_audit_studio.scanner.config import DEFAULT_RESOLUTIONS, ScanConfig
from dataset_audit_studio.scanner.discovery import discover_media
from dataset_audit_studio.scanner.metrics import (
    METRICS_ALGORITHM_VERSION,
    calculate_metrics,
    perceptual_hashes,
    pixel_sha256,
)
from dataset_audit_studio.scanner.resolution import assess_resolutions
from dataset_audit_studio.scanner.types import MetricEvidence
from PIL import Image, ImageDraw, ImageFilter


def _save_rgb(path: Path, color: tuple[int, int, int] = (30, 90, 180)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 24), color).save(path)


def test_discovery_recursive_toggle_exclusions_and_artist_scope(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _save_rgb(source / "root.png")
    _save_rgb(source / "artist_a" / "nested" / "work.webp")
    _save_rgb(source / ".mikazuki-cache" / "cached.png")

    recursive = discover_media(source, ScanConfig(recursive=True))
    assert [item.relative_path for item in recursive.items] == [
        "artist_a/nested/work.webp",
        "root.png",
    ]
    assert recursive.items[1].artist_scope.encode("ascii").hex() == "5f5f726f6f745f5f"
    assert [item.artist_scope for item in recursive.items] == ["artist_a", "__root__"]
    assert recursive.ignored_directory_count == 1

    flat = discover_media(source, ScanConfig(recursive=False))
    assert [item.relative_path for item in flat.items] == ["root.png"]


def test_discovery_skips_directory_symlinks_when_supported(tmp_path: Path) -> None:
    source = tmp_path / "source"
    external = tmp_path / "external"
    source.mkdir()
    external.mkdir()
    _save_rgb(external / "outside.png")
    try:
        os.symlink(external, source / "linked", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Directory symlinks are unavailable: {error}")

    result = discover_media(source, ScanConfig())
    assert result.items == ()
    assert result.ignored_reparse_count == 1


def test_discovery_ignores_annotation_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "orphan.txt").write_text("unused", encoding="utf-8")
    (source / "metadata.json").write_text('{"prompt":"unused"}', encoding="utf-8")

    result = discover_media(source, ScanConfig())
    assert result.items == ()
    assert not hasattr(result, "orphan_caption_count")


@pytest.mark.parametrize("resolution", DEFAULT_RESOLUTIONS)
def test_resolution_threshold_is_exact_total_pixel_area(resolution: int) -> None:
    config = ScanConfig(resolutions=(resolution,))
    exact = assess_resolutions(resolution, resolution, config)[0]
    below = assess_resolutions(resolution * resolution - 1, 1, config)[0]

    assert exact.minimum_area == resolution * resolution
    assert exact.area_pixels == exact.minimum_area
    assert exact.area_pass is True
    assert exact.eligible is True
    assert below.area_pixels == exact.minimum_area - 1
    assert below.area_pass is False
    assert below.eligible is False
    assert "area_below_resolution" in below.risk_codes


def test_short_side_does_not_override_total_area_rule() -> None:
    result = assess_resolutions(
        2048,
        768,
        ScanConfig(resolutions=(1216,), maximum_aspect_ratio=10),
    )[0]
    assert result.area_pixels >= 1216 * 1216
    assert result.eligible is True


def test_hashes_and_metrics_are_bounded_and_blur_is_review_only() -> None:
    image = Image.effect_noise((96, 80), 64).convert("RGB")
    blurred = image.filter(ImageFilter.GaussianBlur(radius=20))

    digest = pixel_sha256(image)
    expected = hashlib.sha256()
    expected.update(b"rgba8_display_v1:96x80:")
    expected.update(image.convert("RGBA").tobytes())
    assert digest == expected.hexdigest()

    phash, colorhash = perceptual_hashes(image)
    assert len(phash) == 36
    assert len(colorhash) > 0

    metrics = {metric.code: metric for metric in calculate_metrics(blurred, ScanConfig())}
    for code in (
        "black_pixel_ratio",
        "white_pixel_ratio",
        "high_frequency_ratio",
        "edge_density",
        "black_border_ratio",
        "white_border_ratio",
        "transparent_pixel_ratio",
        "fully_transparent_ratio",
        "alpha_edge_ratio",
        "mean_saturation",
    ):
        assert 0 <= float(metrics[code].value) <= 1
    assert metrics["laplacian_variance"].review_only is True
    assert metrics["laplacian_variance"].metadata["never_auto_exclude_alone"] is True


def _metric_map(image: Image.Image) -> dict[str, MetricEvidence]:
    return {metric.code: metric for metric in calculate_metrics(image, ScanConfig())}


def test_border_metrics_require_continuous_opposing_strips() -> None:
    black_bars = Image.new("RGB", (200, 100), (128, 128, 128))
    black_draw = ImageDraw.Draw(black_bars)
    black_draw.rectangle((0, 0, 199, 3), fill=(0, 0, 0))
    black_draw.rectangle((0, 96, 199, 99), fill=(0, 0, 0))
    black = _metric_map(black_bars)
    assert float(black["black_border_ratio"].value) == pytest.approx(0.08)
    assert black["black_border_ratio"].severity == "medium"
    assert float(black["white_border_ratio"].value) == 0.0

    white_bars = Image.new("RGB", (200, 100), (128, 128, 128))
    white_draw = ImageDraw.Draw(white_bars)
    white_draw.rectangle((0, 0, 3, 99), fill=(255, 255, 255))
    white_draw.rectangle((196, 0, 199, 99), fill=(255, 255, 255))
    white = _metric_map(white_bars)
    assert float(white["white_border_ratio"].value) == pytest.approx(0.04)
    assert white["white_border_ratio"].severity == "medium"
    assert float(white["black_border_ratio"].value) == 0.0

    thin_bars = Image.new("RGB", (200, 100), (128, 128, 128))
    thin_draw = ImageDraw.Draw(thin_bars)
    thin_draw.line((0, 0, 199, 0), fill=(0, 0, 0))
    thin_draw.line((0, 99, 199, 99), fill=(0, 0, 0))
    thin = _metric_map(thin_bars)
    assert float(thin["black_border_ratio"].value) == pytest.approx(0.02)
    assert thin["black_border_ratio"].severity == "info"


def test_border_metrics_reject_single_or_noncontinuous_edge_backgrounds() -> None:
    single = Image.new("RGB", (200, 100), (128, 128, 128))
    ImageDraw.Draw(single).rectangle((0, 0, 199, 3), fill=(0, 0, 0))
    single_metrics = _metric_map(single)
    assert float(single_metrics["black_border_ratio"].value) == 0.0
    assert single_metrics["black_border_ratio"].severity == "info"

    irregular = Image.new("RGB", (200, 100), (128, 128, 128))
    irregular_draw = ImageDraw.Draw(irregular)
    irregular_draw.rectangle((0, 0, 197, 3), fill=(0, 0, 0))
    irregular_draw.rectangle((0, 96, 197, 99), fill=(0, 0, 0))
    irregular_metrics = _metric_map(irregular)
    assert float(irregular_metrics["black_border_ratio"].value) == 0.0
    assert irregular_metrics["black_border_ratio"].severity == "info"

    solid_black = _metric_map(Image.new("RGB", (200, 100), (0, 0, 0)))
    assert float(solid_black["black_border_ratio"].value) == 0.0
    assert solid_black["black_pixel_ratio"].severity == "medium"


def test_technical_metrics_use_v2_as_source_and_metadata() -> None:
    metrics = calculate_metrics(Image.new("RGB", (32, 32), "gray"), ScanConfig())
    assert METRICS_ALGORITHM_VERSION == "technical_metrics_v2"
    assert {metric.source for metric in metrics} == {METRICS_ALGORITHM_VERSION}
    assert {
        metric.metadata["algorithm_version"] for metric in metrics
    } == {METRICS_ALGORITHM_VERSION}
