from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DatasetExportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    aesthetic_bins: Literal["disabled", "score_x2_floor"] = "disabled"
    # A curated minimum is intentionally unset until an operator opts in.
    aesthetic_minimum: float | None = Field(default=None, ge=1.0, le=5.0)
    batch_size: int = Field(default=64, ge=1, le=1024)
    refuse_nonempty_output: bool = True
    mode: Literal["copy", "rewrite"] = "copy"
    backup_enabled: bool = True
    rewrite_preview_digest: str | None = Field(default=None, min_length=64, max_length=64)
    keep_latent_files: bool = True
    keep_annotation_files: bool = True

    @classmethod
    def from_task_config(cls, config: dict[str, Any]) -> DatasetExportConfig:
        candidate = config.get("export", {})
        if not isinstance(candidate, dict):
            raise ValueError("export config must be an object")
        return cls.model_validate(candidate)
