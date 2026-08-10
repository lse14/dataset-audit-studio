from __future__ import annotations

import math

from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.types import ResolutionResult


def choose_bucket(width: int, height: int, resolution: int, step: int) -> tuple[int, int]:
    target_area = resolution * resolution
    image_aspect = width / height
    minimum_side = max(step, (min(256, resolution // 4) // step) * step)
    maximum_side = max(resolution, ((2 * resolution) // step) * step)
    candidates: set[tuple[int, int]] = set()

    for bucket_width in range(minimum_side, maximum_side + 1, step):
        ideal_height = target_area / bucket_width
        base_height = max(minimum_side, int(ideal_height // step) * step)
        for offset in (-step, 0, step):
            bucket_height = base_height + offset
            if bucket_height < minimum_side or bucket_height > maximum_side:
                continue
            if bucket_width * bucket_height <= target_area:
                candidates.add((bucket_width, bucket_height))

    if not candidates:
        fallback = max(step, (resolution // step) * step)
        return fallback, fallback

    def score(candidate: tuple[int, int]) -> tuple[float, float, int, int]:
        bucket_width, bucket_height = candidate
        aspect_error = abs(math.log((bucket_width / bucket_height) / image_aspect))
        unused_area = (target_area - bucket_width * bucket_height) / target_area
        return aspect_error, unused_area, bucket_width, bucket_height

    return min(candidates, key=score)


def assess_resolutions(width: int, height: int, config: ScanConfig) -> tuple[ResolutionResult, ...]:
    area = width * height
    aspect = width / height
    extreme_aspect = max(aspect, 1 / aspect) > config.maximum_aspect_ratio
    results: list[ResolutionResult] = []
    for resolution in config.resolutions:
        minimum_area = resolution * resolution
        area_pass = area >= minimum_area
        bucket_width, bucket_height = choose_bucket(width, height, resolution, config.bucket_step)
        scale = max(bucket_width / width, bucket_height / height)
        scaled_area = (width * scale) * (height * scale)
        crop_loss = max(0.0, min(1.0, 1.0 - (bucket_width * bucket_height) / scaled_area))
        upscale_factor = max(1.0, scale)
        risks: list[str] = []
        if not area_pass:
            risks.append("area_below_resolution")
        if extreme_aspect:
            risks.append("extreme_aspect_ratio")
        if crop_loss > config.crop_loss_warning:
            risks.append("high_crop_loss")
        if upscale_factor > config.upscale_warning:
            risks.append("upscale_required")
        results.append(
            ResolutionResult(
                resolution=resolution,
                area_pixels=area,
                minimum_area=minimum_area,
                area_pass=area_pass,
                bucket_width=bucket_width,
                bucket_height=bucket_height,
                upscale_factor=upscale_factor,
                crop_loss=crop_loss,
                aspect_ratio=aspect,
                eligible=area_pass,
                risk_codes=tuple(risks),
            )
        )
    return tuple(results)
