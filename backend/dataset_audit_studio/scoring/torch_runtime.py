from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageOps

from dataset_audit_studio.components.aesthetic_domain.config import AestheticDomainConfig
from dataset_audit_studio.components.aesthetic_domain.runtime import AestheticDomainRuntime
from dataset_audit_studio.components.ai_detection.config import (
    UFD_MODEL_ID,
    AIDetectionConfig,
)
from dataset_audit_studio.components.ai_detection.runtime import AIDetectionRuntime
from dataset_audit_studio.components.clip_features.config import ClipFeatureConfig
from dataset_audit_studio.components.clip_features.runtime import (
    AESTHETIC_FEATURE_CAPABILITY,
    UFD_FEATURE_CAPABILITY,
    ClipFeatureRuntime,
    build_ufd_transform,
    load_pinned_clip_state_dict,
)
from dataset_audit_studio.components.ocr_evidence.config import OCREvidenceConfig
from dataset_audit_studio.components.ocr_evidence.runtime import OCREvidenceRuntime
from dataset_audit_studio.components.watermark_evidence.config import WatermarkEvidenceConfig
from dataset_audit_studio.components.watermark_evidence.runtime import (
    WatermarkEvidenceRuntime,
)
from dataset_audit_studio.core.model_assets import (
    RuntimeAssets,
    verify_runtime_asset_snapshot,
)
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.types import SampleInput, SampleScore

__all__ = ["TorchScoringRuntime", "build_ufd_transform", "load_pinned_clip_state_dict"]


class TorchScoringRuntime:
    """Compatibility facade while scoring components migrate to separate processes."""

    def __init__(self, config: ScoringConfig, assets: RuntimeAssets) -> None:
        verify_runtime_asset_snapshot(assets)
        self.config = config
        self.clip_runtime: ClipFeatureRuntime | None = None
        self.aesthetic_runtime: AestheticDomainRuntime | None = None
        self.ai_runtime: AIDetectionRuntime | None = None
        self.ocr_runtime: OCREvidenceRuntime | None = None
        self.watermark_runtime: WatermarkEvidenceRuntime | None = None
        components = set(config.enabled_components)
        try:
            if "aesthetic" in components or (
                "ai" in components and config.ai.model_id == UFD_MODEL_ID
            ):
                self.clip_runtime = ClipFeatureRuntime(
                    ClipFeatureConfig(device=config.device, precision=config.precision),
                    assets,
                )
            if "aesthetic" in components:
                self.aesthetic_runtime = AestheticDomainRuntime(
                    AestheticDomainConfig(
                        device=config.device,
                        precision=config.precision,
                        model_id=config.aesthetic.model_id,
                        jtp_max_sequence=config.aesthetic.jtp_max_sequence,
                    ),
                    assets,
                )
            if "ai" in components:
                self.ai_runtime = AIDetectionRuntime(
                    AIDetectionConfig(
                        device=config.device,
                        precision=config.precision,
                        model_id=config.ai.model_id,
                    ),
                    assets,
                )
            if "ocr" in components:
                self.ocr_runtime = OCREvidenceRuntime(
                    OCREvidenceConfig(
                        device=config.device,
                        precision=config.precision,
                        bitmap_threshold=config.ocr.bitmap_threshold,
                        box_threshold=config.ocr.box_threshold,
                        unclip_ratio=config.ocr.unclip_ratio,
                        min_size=config.ocr.min_size,
                        max_candidates=config.ocr.max_candidates,
                        recognition_batch_size=config.ocr.recognition_batch_size,
                    ),
                    assets,
                )
            if "watermark" in components:
                self.watermark_runtime = WatermarkEvidenceRuntime(
                    WatermarkEvidenceConfig(
                        device=config.device,
                        precision=config.precision,
                    ),
                    assets,
                )
        except Exception:
            self.close()
            raise

    def score_batch(self, samples: tuple[SampleInput, ...]) -> tuple[SampleScore, ...]:
        images = self._load_images(samples)
        try:
            results = {sample.sample_id: {} for sample in samples}
            capabilities = []
            if self.aesthetic_runtime is not None:
                capabilities.append(AESTHETIC_FEATURE_CAPABILITY)
            if self.ai_runtime is not None and self.config.ai.model_id == UFD_MODEL_ID:
                capabilities.append(UFD_FEATURE_CAPABILITY)
            features = None
            if capabilities:
                if self.clip_runtime is None:
                    raise RuntimeError("CLIP feature runtime was not initialized")
                features = self.clip_runtime.extract(
                    images,
                    tuple(sample.sample_id for sample in samples),
                    tuple(capabilities),
                )
            if self.aesthetic_runtime is not None:
                assert features is not None
                values = self.aesthetic_runtime.score(
                    images,
                    features.get(AESTHETIC_FEATURE_CAPABILITY),
                )
                for sample, result in zip(samples, values, strict=True):
                    results[sample.sample_id]["aesthetic"] = result
            if self.ai_runtime is not None:
                if self.config.ai.model_id == UFD_MODEL_ID:
                    assert features is not None
                    values = self.ai_runtime.score(features.get(UFD_FEATURE_CAPABILITY))
                else:
                    values = self.ai_runtime.score(images)
                for sample, result in zip(samples, values, strict=True):
                    results[sample.sample_id]["ai"] = result
            if self.ocr_runtime is not None:
                for sample, result in zip(
                    samples,
                    self.ocr_runtime.score(images),
                    strict=True,
                ):
                    results[sample.sample_id]["ocr"] = result
            if self.watermark_runtime is not None:
                for sample, result in zip(
                    samples,
                    self.watermark_runtime.score(images),
                    strict=True,
                ):
                    results[sample.sample_id]["watermark"] = result
            self._require_finite(results)
            return tuple(
                SampleScore(sample_id=sample.sample_id, results=results[sample.sample_id])
                for sample in samples
            )
        finally:
            for image in images:
                image.close()

    def close(self) -> None:
        for name in (
            "watermark_runtime",
            "ocr_runtime",
            "ai_runtime",
            "aesthetic_runtime",
            "clip_runtime",
        ):
            runtime = getattr(self, name, None)
            if runtime is not None:
                runtime.close()
                setattr(self, name, None)

    @staticmethod
    def _load_images(samples: tuple[SampleInput, ...]) -> tuple[Image.Image, ...]:
        images: list[Image.Image] = []
        try:
            for sample in samples:
                with Image.open(sample.image_path) as source:
                    source.load()
                    images.append(ImageOps.exif_transpose(source).copy())
            return tuple(images)
        except Exception:
            for image in images:
                image.close()
            raise

    @classmethod
    def _require_finite(cls, value: Any, *, path: str = "result") -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise RuntimeError(f"Non-finite model output at {path}")
        if isinstance(value, dict):
            for key, item in value.items():
                cls._require_finite(item, path=f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                cls._require_finite(item, path=f"{path}[{index}]")
