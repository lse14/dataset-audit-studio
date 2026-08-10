from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from dataset_audit_studio.adapters.component_run_repository import (
    ComponentRunRepository,
)
from dataset_audit_studio.api.component_schemas import (
    BuiltinProfileListResponse,
    BuiltinProfileResponse,
    ComponentListResponse,
    ComponentManifestResponse,
    ComponentRunListResponse,
    ComponentRunResponse,
    RuntimeTuningRecommendationResponse,
)
from dataset_audit_studio.app.component_schema_catalog import (
    component_config_contract,
    component_ui_contract,
)
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.app.runtime_tuning import (
    hardware_snapshot,
    recommend_runtime_tuning,
)
from dataset_audit_studio.core.component_registry import ComponentRegistry, ComponentRegistryError
from dataset_audit_studio.core.profile_contracts import (
    profile_owned_component_ids,
    profile_owned_config_fields,
)
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.presets.builtin import (
    PROFILE_SPECS,
)

router = APIRouter(prefix="/components", tags=["components"])


def _registry(request: Request) -> ComponentRegistry:
    return request.app.state.component_registry


def _manifest_payload(payload: dict) -> dict:
    schema, default = component_config_contract(payload["id"])
    return {
        **payload,
        **component_ui_contract(payload["id"]),
        "json_schema": schema,
        "default_config": default,
    }


@router.get("", response_model=ComponentListResponse)
def list_components(request: Request) -> ComponentListResponse:
    payloads = tuple(_manifest_payload(item) for item in _registry(request).manifest_payloads())
    return ComponentListResponse(
        items=[ComponentManifestResponse.model_validate(item) for item in payloads],
        total=len(payloads),
    )


@router.get("/builtin-profiles", response_model=BuiltinProfileListResponse)
def list_builtin_profiles(request: Request) -> BuiltinProfileListResponse:
    registry = _registry(request)
    items = [
        BuiltinProfileResponse(
            id=spec.id,
            display_name=spec.display_name,
            description=spec.description,
            scope_mode=spec.scope_mode,
            profile_owned_component_ids=list(profile_owned_component_ids(spec.id)),
            profile_owned_config_fields={
                component_id: list(fields)
                for component_id, fields in profile_owned_config_fields(spec.id).items()
            },
            components=materialize_profile(spec.id, registry=registry)["components"],
        )
        for spec in PROFILE_SPECS
    ]
    return BuiltinProfileListResponse(items=items, total=len(items))


@router.get(
    "/runtime-tuning/recommendation",
    response_model=RuntimeTuningRecommendationResponse,
)
def runtime_tuning_recommendation() -> RuntimeTuningRecommendationResponse:
    return RuntimeTuningRecommendationResponse.model_validate(
        recommend_runtime_tuning(hardware_snapshot())
    )


@router.get("/{component_id}", response_model=ComponentManifestResponse)
def get_component(component_id: str, request: Request) -> ComponentManifestResponse:
    try:
        definition = _registry(request).get(component_id)
    except ComponentRegistryError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ComponentManifestResponse.model_validate(
        _manifest_payload(definition.manifest.public_dict())
    )


@router.get("/runs/{task_id}", response_model=ComponentRunListResponse)
def list_component_runs(task_id: str, request: Request) -> ComponentRunListResponse:
    database: Database = request.app.state.database
    task = TaskService(database).get_task(task_id)
    with database.read_session() as session:
        runs = ComponentRunRepository().list_for_config(
            session,
            task_id=task.id,
            config_hash=task.config_hash,
        )
    items = [
        ComponentRunResponse.model_validate(
            {
                **item.__dict__,
                "dependency_ids": list(item.dependency_ids),
                "model_ids": list(item.model_ids),
            }
        )
        for item in runs
    ]
    return ComponentRunListResponse(
        task_id=task.id,
        config_hash=task.config_hash,
        items=items,
        total=len(items),
    )
