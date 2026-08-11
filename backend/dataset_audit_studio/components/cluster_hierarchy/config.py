from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HierarchyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_mode: Literal["global", "artist", "concept"] = "artist"
    minimum_split_size: int = Field(default=64, ge=8, le=4096)
    target_leaf_size: int = Field(default=128, ge=16, le=8192)
    max_branching: int = Field(default=32, ge=2, le=64)
    kmeans_iterations: int = Field(default=25, ge=5, le=200)
    seed: int = Field(default=20260717, ge=0, le=2**31 - 1)
    semantic_duplicate_threshold: float = Field(default=0.92, ge=0.8, le=1.0)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> HierarchyConfig:
        if self.minimum_split_size > self.target_leaf_size:
            raise ValueError("minimum_split_size cannot exceed target_leaf_size")
        return self
