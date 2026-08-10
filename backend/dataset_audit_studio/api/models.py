from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from dataset_audit_studio.api.model_schemas import (
    LocalModelRegisterRequest,
    ModelDownloadRequest,
    ModelListResponse,
    ModelOperationResponse,
    ModelVerifyResponse,
)
from dataset_audit_studio.model_adapters.service import ModelService
from dataset_audit_studio.model_adapters.types import ModelStatus, OperationSnapshot

router = APIRouter(prefix="/models", tags=["models"])


def _service(request: Request) -> ModelService:
    return request.app.state.model_service


@router.get("", response_model=ModelListResponse)
def list_models(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    purpose: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    installation_status: Annotated[
        str | None,
        Query(alias="status", pattern=r"^[a-z_]+$"),
    ] = None,
) -> ModelListResponse:
    service = _service(request)
    items, total = service.list_models(
        offset=offset,
        limit=limit,
        purpose=purpose,
        installation_status=installation_status,
    )
    return ModelListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        registry_version=service.registry.document.registry_version,
        registry_digest=service.registry.digest,
    )


@router.post(
    "/download-all",
    response_model=ModelOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def download_all(request: Request) -> ModelOperationResponse:
    return ModelOperationResponse(operations=list(_service(request).download_all()))


@router.post("/local", response_model=ModelStatus, status_code=status.HTTP_201_CREATED)
def register_local(payload: LocalModelRegisterRequest, request: Request) -> ModelStatus:
    return _service(request).register_local(
        base_model_id=payload.base_model_id,
        source_path=Path(payload.source_path).expanduser(),
        display_name=payload.display_name,
    )


@router.get("/{model_id}", response_model=ModelStatus)
def get_model(model_id: str, request: Request) -> ModelStatus:
    return _service(request).get_model(model_id)


@router.post(
    "/{model_id}/download",
    response_model=ModelOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def download_model(
    model_id: str,
    payload: ModelDownloadRequest,
    request: Request,
) -> ModelOperationResponse:
    return ModelOperationResponse(
        operations=list(
            _service(request).download(
                model_id,
                include_dependencies=payload.include_dependencies,
            )
        )
    )


@router.post("/{model_id}/cancel", response_model=OperationSnapshot)
def cancel_model_operation(model_id: str, request: Request) -> OperationSnapshot:
    return _service(request).cancel(model_id)


@router.post("/{model_id}/verify", response_model=ModelVerifyResponse)
def verify_model(model_id: str, request: Request) -> ModelVerifyResponse:
    model, operation = _service(request).verify(model_id)
    return ModelVerifyResponse(model=model, operation=operation)
