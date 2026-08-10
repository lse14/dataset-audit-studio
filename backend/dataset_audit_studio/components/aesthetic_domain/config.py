from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dataset_audit_studio.core.torch_runtime import DeviceRequest, Precision


class AestheticDomainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: DeviceRequest = "auto"
    precision: Precision = "float32"
    model_id: str = "aesthetic_lse14_5k"
    in_domain_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    jtp_max_sequence: int = Field(default=1024, ge=64, le=1024)
