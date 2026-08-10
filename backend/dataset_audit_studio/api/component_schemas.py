from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from dataset_audit_studio.core.profile_contracts import DatasetProfile


class CapabilityRequirementResponse(BaseModel):
    capability: str
    optional: bool


class ComponentManifestResponse(BaseModel):
    id: str
    version: str
    phase_order: int
    config_schema: str
    consumes: list[CapabilityRequirementResponse]
    produces: list[str]
    model_ids: list[str]
    execution: Literal["cpu_inline", "cpu_process", "gpu_process"]
    failure_policy: Literal["stop"]
    default_enabled: bool
    display_name: str
    ui_group: str
    activation: Literal["required", "auto", "optional"]
    recommended_enabled: bool
    json_schema: dict[str, Any]
    default_config: dict[str, Any]


class ComponentListResponse(BaseModel):
    items: list[ComponentManifestResponse]
    total: int


class BuiltinProfileResponse(BaseModel):
    id: DatasetProfile
    display_name: str
    description: str
    scope_mode: Literal["concept", "global"]
    profile_owned_component_ids: list[str]
    profile_owned_config_fields: dict[str, list[str]]
    components: dict[str, dict[str, Any]]


class BuiltinProfileListResponse(BaseModel):
    items: list[BuiltinProfileResponse]
    total: int


class RuntimeHardwareResponse(BaseModel):
    cuda_available: bool
    free_vram_bytes: int | None
    total_vram_bytes: int | None
    available_memory_bytes: int | None


class RuntimeTuningRecommendationResponse(BaseModel):
    hardware: RuntimeHardwareResponse
    device: Literal["cpu", "cuda"]
    precision: Literal["float32", "float16"]
    updates: dict[str, dict[str, Any]]


class ComponentRunResponse(BaseModel):
    component_id: str
    component_version: str
    phase: str
    phase_order: int
    execution: Literal["cpu_inline", "cpu_process", "gpu_process"]
    status: Literal["pending", "running", "paused", "completed", "terminated", "failed"]
    config_hash: str
    config_digest: str
    input_digest: str | None
    model_digest: str | None
    normalized_config: dict[str, Any]
    dependency_ids: list[str]
    model_ids: list[str]
    checkpoint: dict[str, Any]
    completed_items: int
    total_items: int | None
    auto_enabled: bool
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ComponentRunListResponse(BaseModel):
    task_id: str
    config_hash: str
    items: list[ComponentRunResponse]
    total: int
