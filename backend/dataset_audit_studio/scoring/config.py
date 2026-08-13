from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataset_audit_studio.components.ai_detection.config import UFD_MODEL_ID, AIModelId


class _StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AestheticScoringConfig(_StrictConfig):
    enabled: bool = False
    model_id: str = "aesthetic_lse14_5k"
    in_domain_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    jtp_max_sequence: int = Field(default=1024, ge=64, le=1024)


class AIScoringConfig(_StrictConfig):
    enabled: bool = False
    # Configs saved before component model selection retain their UFD semantics.
    model_id: AIModelId = UFD_MODEL_ID
    candidate_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    reference_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> AIScoringConfig:
        if self.candidate_threshold > self.reference_threshold:
            raise ValueError("AI candidate_threshold cannot exceed reference_threshold")
        return self


class OCRScoringConfig(_StrictConfig):
    enabled: bool = False
    bitmap_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    box_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    unclip_ratio: float = Field(default=1.5, gt=0.0, le=10.0)
    min_size: int = Field(default=3, ge=1, le=128)
    max_candidates: int = Field(default=1000, ge=1, le=5000)
    recognition_batch_size: int = Field(default=16, ge=1, le=256)
    text_density_threshold: float = Field(default=0.25, ge=0.0, le=1.0)


class WatermarkScoringConfig(_StrictConfig):
    enabled: bool = False
    review_threshold: float = Field(default=0.995, ge=0.0, le=1.0)


class ScoringConfig(_StrictConfig):
    enabled: bool = True
    device: Literal["auto", "cuda", "cpu"] = "auto"
    precision: Literal["float32", "float16", "bfloat16"] = "float32"
    batch_size: int = Field(default=1, ge=1, le=256)
    aesthetic: AestheticScoringConfig = Field(default_factory=AestheticScoringConfig)
    ai: AIScoringConfig = Field(default_factory=AIScoringConfig)
    ocr: OCRScoringConfig = Field(default_factory=OCRScoringConfig)
    watermark: WatermarkScoringConfig = Field(default_factory=WatermarkScoringConfig)

    @classmethod
    def from_task_config(cls, task_config: dict[str, Any]) -> ScoringConfig:
        raw = task_config.get("scoring", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise TypeError("scoring config must be an object")
        return cls.model_validate(raw)

    @property
    def enabled_components(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        return tuple(
            name
            for name, component in (
                ("aesthetic", self.aesthetic),
                ("ai", self.ai),
                ("ocr", self.ocr),
                ("watermark", self.watermark),
            )
            if component.enabled
        )

    def inference_config(self, component: str) -> dict[str, Any]:
        common = {"precision": self.precision}
        if component == "aesthetic":
            return {
                **common,
                "model_id": self.aesthetic.model_id,
                "jtp_max_sequence": self.aesthetic.jtp_max_sequence,
            }
        if component == "ai":
            preprocess = (
                "center_crop_224"
                if self.ai.model_id == UFD_MODEL_ID
                else "resize_440_center_crop_384_imagenet_v1"
            )
            return {**common, "model_id": self.ai.model_id, "preprocess": preprocess}
        if component == "ocr":
            return {
                **common,
                "bitmap_threshold": self.ocr.bitmap_threshold,
                "box_threshold": self.ocr.box_threshold,
                "unclip_ratio": self.ocr.unclip_ratio,
                "min_size": self.ocr.min_size,
                "max_candidates": self.ocr.max_candidates,
            }
        if component == "watermark":
            return {**common, "labels_from_config": True}
        raise ValueError(f"Unknown scoring component: {component}")

    def inference_config_hash(self, component: str) -> str:
        payload = json.dumps(
            self.inference_config(component),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
