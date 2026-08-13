from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SemanticEmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: Literal["auto", "cuda", "cpu"] = "auto"
    batch_size: int = Field(default=8, ge=1, le=256)
    shard_size: int = Field(default=4096, ge=64, le=16_384)
