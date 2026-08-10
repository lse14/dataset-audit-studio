from __future__ import annotations

import hashlib
import math

import cv2
import imagehash
import numpy as np
from PIL import Image

from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.types import MetricEvidence

METRICS_ALGORITHM_VERSION = "technical_metrics_v2"
PIXEL_HASH_VERSION = "rgba8_display_v1"

_BORDER_SCANLINE_COVERAGE = 0.995
_BORDER_MINIMUM_DEPTH_RATIO = 0.005
_BORDER_INTERIOR_COVERAGE_DROP = 0.60


def _analysis_image(image: Image.Image, max_side: int) -> Image.Image:
    analysis = image.copy()
    analysis.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return analysis


def _visible_rgb(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[:, :, 3]
    foreground = rgba[:, :, :3].astype(np.float32)
    alpha_fraction = alpha.astype(np.float32)[:, :, None] / 255.0
    composite = foreground * alpha_fraction + 255.0 * (1.0 - alpha_fraction)
    return composite.astype(np.uint8), alpha


def _entropy(channel: np.ndarray) -> float:
    counts = np.bincount(channel.reshape(-1), minlength=256).astype(np.float64)
    probabilities = counts[counts > 0] / counts.sum()
    return float(-(probabilities * np.log2(probabilities)).sum())


def _high_frequency_ratio(gray: np.ndarray, max_side: int) -> float:
    height, width = gray.shape
    if max(height, width) > max_side:
        scale = max_side / max(height, width)
        gray = cv2.resize(
            gray,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    values = gray.astype(np.float32)
    spectrum = np.fft.fftshift(np.fft.fft2(values))
    power = np.abs(spectrum) ** 2
    center_y, center_x = np.array(power.shape) // 2
    total = float(power.sum() - power[center_y, center_x])
    if total <= 0:
        return 0.0
    y, x = np.ogrid[: power.shape[0], : power.shape[1]]
    radius = np.sqrt((y - center_y) ** 2 + (x - center_x) ** 2)
    cutoff = min(power.shape) * 0.25
    return float(power[radius >= cutoff].sum() / total)


def _edge_strip_depth(coverages: np.ndarray) -> int:
    depth = 0
    for coverage in coverages:
        if coverage < _BORDER_SCANLINE_COVERAGE:
            break
        depth += 1
    minimum_depth = max(1, math.ceil(len(coverages) * _BORDER_MINIMUM_DEPTH_RATIO))
    if depth < minimum_depth or depth >= len(coverages):
        return 0
    interior_drop = float(coverages[:depth].mean()) - float(coverages[depth])
    if interior_drop < _BORDER_INTERIOR_COVERAGE_DROP:
        return 0
    return depth


def _opposing_strip_ratio(coverages: np.ndarray) -> float:
    start_depth = _edge_strip_depth(coverages)
    end_depth = _edge_strip_depth(coverages[::-1])
    if not start_depth or not end_depth or start_depth + end_depth >= len(coverages):
        return 0.0
    return float((start_depth + end_depth) / len(coverages))


def _paired_border_ratio(mask: np.ndarray) -> float:
    horizontal = _opposing_strip_ratio(mask.mean(axis=1))
    vertical = _opposing_strip_ratio(mask.mean(axis=0))
    return max(horizontal, vertical)


def _border_ratios(gray: np.ndarray) -> tuple[float, float]:
    return (
        _paired_border_ratio(gray <= 12),
        _paired_border_ratio(gray >= 243),
    )


def _blockiness(gray: np.ndarray) -> float:
    if min(gray.shape) < 16:
        return 0.0
    values = gray.astype(np.float32)
    horizontal_diffs = np.abs(np.diff(values, axis=1))
    vertical_diffs = np.abs(np.diff(values, axis=0))
    horizontal_boundary = horizontal_diffs[:, 7::8]
    vertical_boundary = vertical_diffs[7::8, :]
    horizontal_nonboundary = np.delete(
        horizontal_diffs, np.arange(7, horizontal_diffs.shape[1], 8), axis=1
    )
    vertical_nonboundary = np.delete(
        vertical_diffs, np.arange(7, vertical_diffs.shape[0], 8), axis=0
    )
    boundary = np.concatenate((horizontal_boundary.ravel(), vertical_boundary.ravel()))
    nonboundary = np.concatenate((horizontal_nonboundary.ravel(), vertical_nonboundary.ravel()))
    boundary_mean = float(boundary.mean()) if boundary.size else 0.0
    nonboundary_mean = float(nonboundary.mean()) if nonboundary.size else 0.0
    return max(0.0, (boundary_mean - nonboundary_mean) / max(nonboundary_mean, 1.0))


def pixel_sha256(image: Image.Image) -> str:
    rgba = image.convert("RGBA")
    digest = hashlib.sha256()
    digest.update(f"{PIXEL_HASH_VERSION}:{rgba.width}x{rgba.height}:".encode("ascii"))
    digest.update(rgba.tobytes())
    return digest.hexdigest()


def perceptual_hashes(image: Image.Image) -> tuple[str, str]:
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    visible = Image.alpha_composite(background, rgba).convert("RGB")
    return str(imagehash.phash(visible, hash_size=12)), str(imagehash.colorhash(visible))


def _metric(
    code: str,
    value: float,
    threshold: float | None,
    *,
    violated: bool = False,
    review_only: bool = False,
    metadata: dict[str, object] | None = None,
) -> MetricEvidence:
    return MetricEvidence(
        code=code,
        value=value,
        threshold=threshold,
        severity="medium" if violated else "info",
        review_only=review_only,
        source=METRICS_ALGORITHM_VERSION,
        metadata=dict(metadata or {}),
    )


def calculate_metrics(image: Image.Image, config: ScanConfig) -> tuple[MetricEvidence, ...]:
    analysis = _analysis_image(image, config.metrics_max_side)
    rgb, alpha = _visible_rgb(analysis)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    thresholds = config.thresholds

    rgb_entropy = sum(_entropy(rgb[:, :, channel]) for channel in range(3)) / 3.0
    black_ratio = float(np.mean(gray <= 12))
    white_ratio = float(np.mean(gray >= 243))
    luminance_std = float(gray.std())
    laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    high_frequency_ratio = _high_frequency_ratio(gray, config.fft_max_side)
    edge_density = float(np.mean(cv2.Canny(gray, 100, 200) > 0))
    black_border, white_border = _border_ratios(gray)
    blockiness = _blockiness(gray)
    transparent_ratio = float(np.mean(alpha < 255))
    fully_transparent_ratio = float(np.mean(alpha == 0))
    alpha_edge_ratio = float(np.mean((alpha > 0) & (alpha < 255)))
    saturation = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    mean_saturation = float(saturation.mean() / 255.0)

    metrics = [
        _metric(
            "rgb_entropy",
            rgb_entropy,
            thresholds.minimum_rgb_entropy,
            violated=rgb_entropy < thresholds.minimum_rgb_entropy,
            metadata={"comparison": "minimum"},
        ),
        _metric(
            "black_pixel_ratio",
            black_ratio,
            thresholds.maximum_black_ratio,
            violated=black_ratio > thresholds.maximum_black_ratio,
            metadata={"comparison": "maximum"},
        ),
        _metric(
            "white_pixel_ratio",
            white_ratio,
            thresholds.maximum_white_ratio,
            violated=white_ratio > thresholds.maximum_white_ratio,
            metadata={"comparison": "maximum"},
        ),
        _metric(
            "luminance_std",
            luminance_std,
            thresholds.minimum_luminance_std,
            violated=luminance_std < thresholds.minimum_luminance_std,
            metadata={"comparison": "minimum"},
        ),
        _metric(
            "laplacian_variance",
            laplacian_variance,
            thresholds.minimum_laplacian_variance,
            violated=laplacian_variance < thresholds.minimum_laplacian_variance,
            review_only=True,
            metadata={"comparison": "minimum", "never_auto_exclude_alone": True},
        ),
        _metric(
            "high_frequency_ratio",
            high_frequency_ratio,
            thresholds.maximum_high_frequency_ratio,
            violated=high_frequency_ratio > thresholds.maximum_high_frequency_ratio,
            metadata={"comparison": "maximum"},
        ),
        _metric("edge_density", edge_density, None),
        _metric(
            "black_border_ratio",
            black_border,
            thresholds.maximum_border_ratio,
            violated=black_border > thresholds.maximum_border_ratio,
            metadata={"comparison": "maximum"},
        ),
        _metric(
            "white_border_ratio",
            white_border,
            thresholds.maximum_border_ratio,
            violated=white_border > thresholds.maximum_border_ratio,
            metadata={"comparison": "maximum"},
        ),
        _metric(
            "blockiness",
            blockiness,
            thresholds.maximum_blockiness,
            violated=blockiness > thresholds.maximum_blockiness,
            metadata={"comparison": "maximum"},
        ),
        _metric("transparent_pixel_ratio", transparent_ratio, None),
        _metric("fully_transparent_ratio", fully_transparent_ratio, 1.0),
        _metric("alpha_edge_ratio", alpha_edge_ratio, None),
        _metric("mean_saturation", mean_saturation, None),
    ]
    for metric in metrics:
        metric.metadata["analysis_width"] = analysis.width
        metric.metadata["analysis_height"] = analysis.height
        metric.metadata["algorithm_version"] = METRICS_ALGORITHM_VERSION
    analysis.close()
    return tuple(metrics)


def is_fully_transparent(metrics: tuple[MetricEvidence, ...]) -> bool:
    value = next(
        (metric.value for metric in metrics if metric.code == "fully_transparent_ratio"), 0.0
    )
    return math.isclose(float(value or 0.0), 1.0, abs_tol=1e-12)
