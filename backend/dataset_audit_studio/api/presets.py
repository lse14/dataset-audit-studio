from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, status

from dataset_audit_studio.api.preset_schemas import (
    TaskPresetCreate,
    TaskPresetDelete,
    TaskPresetDeleteResponse,
    TaskPresetFromTask,
    TaskPresetListResponse,
    TaskPresetResponse,
    TaskPresetUpdate,
)
from dataset_audit_studio.app.component_task_config import (
    ComponentTaskConfigMaterializer,
)
from dataset_audit_studio.jobs.errors import LegacyTaskConfigUnsupported
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.presets.service import (
    TaskPresetError,
    TaskPresetNameConflict,
    TaskPresetNotFound,
    TaskPresetService,
    TaskPresetVersionConflict,
)

router = APIRouter(prefix="/task-presets", tags=["task-presets"])


def _service(request: Request) -> TaskPresetService:
    return TaskPresetService(request.app.state.database)


def _materializer(request: Request) -> ComponentTaskConfigMaterializer:
    return ComponentTaskConfigMaterializer(request.app.state.component_registry)


def _validated_components(request: Request, raw_components: dict, profile: str) -> dict:
    return _materializer(request).materialize(
        raw_components,
        profile=profile,
        require_profile=True,
    )["components"]


def _raise_http_error(error: Exception) -> NoReturn:
    if isinstance(error, LegacyTaskConfigUnsupported):
        raise error
    if isinstance(error, TaskPresetNotFound):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, (TaskPresetNameConflict, TaskPresetVersionConflict)):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.get("", response_model=TaskPresetListResponse)
def list_task_presets(request: Request) -> TaskPresetListResponse:
    items = _service(request).list_presets()
    return TaskPresetListResponse(
        items=[TaskPresetResponse.from_view(item) for item in items],
        total=len(items),
    )


@router.post(
    "",
    response_model=TaskPresetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task_preset(
    payload: TaskPresetCreate,
    request: Request,
) -> TaskPresetResponse:
    try:
        components = _validated_components(request, payload.components, payload.profile)
        preset = _service(request).create(
            name=payload.name,
            components=components,
            profile=payload.profile,
        )
    except (TaskPresetError, TypeError, ValueError) as error:
        _raise_http_error(error)
    return TaskPresetResponse.from_view(preset)


@router.post(
    "/from-task/{task_id}",
    response_model=TaskPresetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task_preset_from_task(
    task_id: str,
    payload: TaskPresetFromTask,
    request: Request,
) -> TaskPresetResponse:
    task = TaskService(request.app.state.database).get_task(task_id)
    try:
        if _materializer(request).profile_from_task_config(task.config) is None:
            raise ValueError(
                "legacy_task_config_unsupported: profile-free tasks cannot become "
                "presets"
            )
        profile = _materializer(request).profile_from_task_config(task.config)
        if profile is None:
            raise ValueError("legacy_task_config_unsupported: profile is required")
        components = _materializer(request).components_from_task_config(task.config)
        preset = _service(request).create(
            name=payload.name,
            components=components,
            profile=profile.value,
        )
    except (TaskPresetError, TypeError, ValueError) as error:
        _raise_http_error(error)
    return TaskPresetResponse.from_view(preset)


@router.put("/{preset_id}", response_model=TaskPresetResponse)
def update_task_preset(
    preset_id: str,
    payload: TaskPresetUpdate,
    request: Request,
) -> TaskPresetResponse:
    try:
        components = _validated_components(request, payload.components, payload.profile)
        preset = _service(request).update(
            preset_id,
            name=payload.name,
            components=components,
            expected_version=payload.expected_version,
            profile=payload.profile,
        )
    except (TaskPresetError, TypeError, ValueError) as error:
        _raise_http_error(error)
    return TaskPresetResponse.from_view(preset)


@router.delete("/{preset_id}", response_model=TaskPresetDeleteResponse)
def delete_task_preset(
    preset_id: str,
    payload: TaskPresetDelete,
    request: Request,
) -> TaskPresetDeleteResponse:
    try:
        deleted = _service(request).delete(
            preset_id,
            expected_version=payload.expected_version,
        )
    except TaskPresetError as error:
        _raise_http_error(error)
    return TaskPresetDeleteResponse(preset_id=deleted.id)
