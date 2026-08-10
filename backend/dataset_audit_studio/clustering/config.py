from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SAEConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    feature_count: int = Field(default=256, ge=8, le=4096)
    epochs: int = Field(default=8, ge=1, le=100)
    batch_size: int = Field(default=256, ge=8, le=4096)
    learning_rate: float = Field(default=1e-3, gt=0.0, le=0.1)
    l1_coefficient: float = Field(default=1e-3, ge=0.0, le=1.0)
    activation_percentile: float = Field(default=95.0, ge=50.0, lt=100.0)
    top_k: int = Field(default=16, ge=1, le=100)
    seed: int = Field(default=20260718, ge=0, le=2**31 - 1)


class ClusteringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    scope_mode: Literal["global", "artist", "concept"] = "artist"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    embedding_batch_size: int = Field(default=8, ge=1, le=64)
    embedding_shard_size: int = Field(default=4096, ge=64, le=16_384)
    minimum_split_size: int = Field(default=64, ge=8, le=4096)
    target_leaf_size: int = Field(default=128, ge=16, le=8192)
    max_branching: int = Field(default=32, ge=2, le=64)
    kmeans_iterations: int = Field(default=25, ge=5, le=200)
    seed: int = Field(default=20260717, ge=0, le=2**31 - 1)
    phash_max_distance: int = Field(default=4, ge=0, le=32)
    colorhash_max_distance: int = Field(default=2, ge=0, le=32)
    semantic_duplicate_threshold: float = Field(default=0.985, ge=0.8, le=1.0)
    sae: SAEConfig = Field(default_factory=SAEConfig)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> ClusteringConfig:
        if self.minimum_split_size > self.target_leaf_size:
            raise ValueError("minimum_split_size cannot exceed target_leaf_size")
        return self

    @classmethod
    def from_task_config(cls, task_config: dict[str, Any]) -> ClusteringConfig:
        raw = task_config.get("clustering", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise TypeError("clustering config must be an object")
        return cls.model_validate(raw)

    def embedding_payload(self) -> dict[str, Any]:
        return {"model": "siglip2_so400m_naflex", "preprocess": "processor-v1"}

    def hierarchy_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            include={
                "scope_mode",
                "minimum_split_size",
                "target_leaf_size",
                "max_branching",
                "kmeans_iterations",
                "seed",
            },
        )


class SelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def from_task_config(cls, task_config: dict[str, Any]) -> SelectionConfig:
        raw = task_config.get("selection", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise TypeError("selection config must be an object")
        return cls.model_validate(raw)
