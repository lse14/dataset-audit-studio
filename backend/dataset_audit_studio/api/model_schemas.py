from __future__ import annotations

from pydantic import BaseModel, Field

from dataset_audit_studio.model_adapters.types import ModelStatus, OperationSnapshot


class ModelListResponse(BaseModel):
    items: list[ModelStatus]
    total: int
    offset: int
    limit: int
    registry_version: str
    registry_digest: str


class ModelDownloadRequest(BaseModel):
    include_dependencies: bool = True


class ModelOperationResponse(BaseModel):
    operations: list[OperationSnapshot]


class ModelVerifyResponse(BaseModel):
    model: ModelStatus
    operation: OperationSnapshot | None


class LocalModelRegisterRequest(BaseModel):
    base_model_id: str = Field(min_length=3, max_length=80)
    source_path: str = Field(min_length=1, max_length=32767)
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
