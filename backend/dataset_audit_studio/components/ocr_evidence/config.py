from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataset_audit_studio.core.torch_runtime import DeviceRequest, Precision


class OCREvidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: DeviceRequest = "auto"
    precision: Precision = "float32"
    bitmap_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    box_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    unclip_ratio: float = Field(default=1.5, gt=0.0, le=10.0)
    min_size: int = Field(default=3, ge=1, le=128)
    max_candidates: int = Field(default=1000, ge=1, le=5000)
    recognition_batch_size: int = Field(default=16, ge=1, le=256)
    text_density_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
