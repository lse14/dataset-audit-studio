from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataset_audit_studio.core.torch_runtime import DeviceRequest, Precision

UFD_MODEL_ID = "universal_fake_detector_head"
COMMUNITY_FORENSICS_MODEL_ID = "community_forensics_model_384"
AIModelId = Literal["universal_fake_detector_head", "community_forensics_model_384"]


class AIDetectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: DeviceRequest = "auto"
    precision: Precision = "float32"
    model_id: AIModelId = COMMUNITY_FORENSICS_MODEL_ID
    candidate_threshold: float = Field(default=0.121558, ge=0.0, le=1.0)
    reference_threshold: float = Field(default=0.464626, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> AIDetectionConfig:
        if self.candidate_threshold > self.reference_threshold:
            raise ValueError("candidate_threshold cannot exceed reference_threshold")
        return self
