from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dataset_audit_studio.api.schemas import ComponentConfigInput
from dataset_audit_studio.core.profile_contracts import DatasetProfile
from dataset_audit_studio.presets.service import TaskPresetView


class _PresetNamePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Task preset name must not be blank")
        return cleaned


class TaskPresetCreate(_PresetNamePayload):
    components: dict[str, ComponentConfigInput] = Field(min_length=1)
    profile: DatasetProfile


class TaskPresetUpdate(TaskPresetCreate):
    expected_version: int = Field(ge=1)


class TaskPresetFromTask(_PresetNamePayload):
    pass


class TaskPresetDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class TaskPresetResponse(BaseModel):
    id: str
    name: str
    components: dict[str, ComponentConfigInput]
    profile: DatasetProfile | None
    row_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: TaskPresetView) -> TaskPresetResponse:
        return cls.model_validate(view, from_attributes=True)


class TaskPresetListResponse(BaseModel):
    items: list[TaskPresetResponse]
    total: int


class TaskPresetDeleteResponse(BaseModel):
    preset_id: str
