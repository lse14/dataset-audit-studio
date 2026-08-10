from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SparseAutoencoderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature_count: int = Field(default=256, ge=8, le=4096)
    epochs: int = Field(default=8, ge=1, le=100)
    batch_size: int = Field(default=256, ge=8, le=4096)
    learning_rate: float = Field(default=1e-3, gt=0.0, le=0.1)
    l1_coefficient: float = Field(default=1e-3, ge=0.0, le=1.0)
    activation_percentile: float = Field(default=95.0, ge=50.0, lt=100.0)
    top_k: int = Field(default=16, ge=1, le=100)
    seed: int = Field(default=20260718, ge=0, le=2**31 - 1)
