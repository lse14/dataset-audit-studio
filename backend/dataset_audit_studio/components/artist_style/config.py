from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StyleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    device: Literal["auto", "cuda", "cpu"] = "auto"
    batch_size: int = Field(default=4, ge=1, le=64)
    image_size: Literal[224] = 224
    minimum_scope_size: int = Field(default=8, ge=2, le=10_000)
    max_iterations: int = Field(default=3, ge=1, le=3)
    outlier_sigma: float = Field(default=0.522, ge=0.5, le=5.0)
    minimum_style_score: float = Field(default=92.07, ge=0.0, le=100.0)
    lsnet_weight: float = Field(default=0.892, ge=0.0, le=1.0)
    gram_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    dino_weight: float = Field(default=0.108, ge=0.0, le=1.0)
    gram_average_weight: float = Field(default=0.8, ge=0.0, le=1.0)
    gram_centroid_weight: float = Field(default=0.2, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def preserve_explicit_legacy_weights(cls, raw: Any) -> Any:
        if not isinstance(raw, dict) or "lsnet_weight" in raw:
            return raw
        if {"gram_weight", "dino_weight"}.issubset(raw):
            values = dict(raw)
            values["lsnet_weight"] = 0.0
            return values
        return raw

    @model_validator(mode="after")
    def validate_weights(self) -> StyleConfig:
        if abs(self.lsnet_weight + self.gram_weight + self.dino_weight - 1.0) > 1e-9:
            raise ValueError("lsnet_weight, gram_weight, and dino_weight must sum to 1")
        if abs(self.gram_average_weight + self.gram_centroid_weight - 1.0) > 1e-9:
            raise ValueError(
                "gram_average_weight and gram_centroid_weight must sum to 1"
            )
        return self

    @classmethod
    def from_task_config(cls, task_config: dict[str, Any]) -> StyleConfig:
        raw = task_config.get("style", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise TypeError("style config must be an object")
        return cls.model_validate(raw)

    def analysis_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"device", "batch_size"},
        )
