from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_RESOLUTIONS = (512, 768, 1024, 1216, 1536)


class MetricThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_rgb_entropy: float = Field(default=2.5, ge=0, le=8)
    maximum_black_ratio: float = Field(default=0.90, ge=0, le=1)
    maximum_white_ratio: float = Field(default=0.90, ge=0, le=1)
    minimum_laplacian_variance: float = Field(default=16.0, ge=0)
    maximum_high_frequency_ratio: float = Field(default=0.32, ge=0, le=1)
    maximum_border_ratio: float = Field(default=0.03, ge=0, le=1)
    maximum_blockiness: float = Field(default=0.35, ge=0)
    minimum_luminance_std: float = Field(default=10.0, ge=0)


class ScanConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    recursive: bool = True
    resolutions: tuple[int, ...] = DEFAULT_RESOLUTIONS
    batch_size: int = Field(default=64, ge=1, le=4096)
    cpu_workers: int = Field(default=4, ge=1, le=16)
    bucket_step: int = Field(default=64, ge=8, le=256)
    maximum_aspect_ratio: float = Field(default=4.0, ge=1.0, le=20.0)
    crop_loss_warning: float = Field(default=0.35, ge=0, le=1)
    upscale_warning: float = Field(default=1.001, ge=1.0, le=8.0)
    metrics_max_side: int = Field(default=1024, ge=128, le=4096)
    fft_max_side: int = Field(default=512, ge=64, le=2048)
    max_decode_pixels: int = Field(default=200_000_000, ge=1_048_576)
    excluded_directory_names: tuple[str, ...] = (
        ".git",
        ".mikazuki-cache",
        "__pycache__",
    )
    thresholds: MetricThresholds = Field(default_factory=MetricThresholds)

    @field_validator("resolutions")
    @classmethod
    def validate_resolutions(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if not values:
            raise ValueError("At least one resolution is required")
        if len(values) > 32:
            raise ValueError("At most 32 resolutions are supported")
        normalized = tuple(sorted(set(values)))
        if any(value < 64 or value > 4096 for value in normalized):
            raise ValueError("Resolutions must be between 64 and 4096")
        return normalized

    @field_validator("bucket_step")
    @classmethod
    def validate_bucket_step(cls, value: int) -> int:
        if value & (value - 1):
            raise ValueError("bucket_step must be a power of two")
        return value

    @field_validator("excluded_directory_names")
    @classmethod
    def normalize_exclusions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({value.strip().casefold() for value in values if value.strip()}))
        if not cleaned:
            raise ValueError("At least one excluded directory name is required")
        return cleaned

    @classmethod
    def from_task_config(cls, config: dict[str, Any]) -> ScanConfig:
        candidate = config.get("scan", config)
        if not isinstance(candidate, dict):
            raise ValueError("scan config must be an object")
        return cls.model_validate(candidate)

    def cache_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
