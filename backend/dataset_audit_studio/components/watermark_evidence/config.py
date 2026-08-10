from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataset_audit_studio.core.torch_runtime import DeviceRequest, Precision


class WatermarkEvidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: DeviceRequest = "auto"
    precision: Precision = "float32"
    review_threshold: float = Field(default=0.995, ge=0.0, le=1.0)
