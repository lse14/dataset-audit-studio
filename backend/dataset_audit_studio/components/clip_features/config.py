from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataset_audit_studio.core.torch_runtime import DeviceRequest, Precision


class ClipFeatureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: DeviceRequest = "auto"
    precision: Precision = "float32"
    batch_size: int = Field(default=4, ge=1, le=256)
