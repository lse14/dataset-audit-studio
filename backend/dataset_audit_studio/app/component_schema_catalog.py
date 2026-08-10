from __future__ import annotations

from typing import Any

from dataset_audit_studio.app.component_registration import (
    BUILTIN_COMPONENT_REGISTRATION_CATALOG,
    ComponentUIContract,
    EmptyComponentConfig,
)

__all__ = (
    "CONFIG_MODELS",
    "UI_CONTRACTS",
    "ComponentUIContract",
    "EmptyComponentConfig",
    "component_config_contract",
    "component_ui_contract",
)

CONFIG_MODELS = BUILTIN_COMPONENT_REGISTRATION_CATALOG.config_models
UI_CONTRACTS = BUILTIN_COMPONENT_REGISTRATION_CATALOG.ui_contracts


def component_config_contract(component_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        model = CONFIG_MODELS[component_id]
    except KeyError as error:
        raise KeyError(f"Component has no config model: {component_id}") from error
    schema = model.model_json_schema(mode="validation")
    default = model().model_dump(mode="json")
    return schema, default


def component_ui_contract(component_id: str) -> dict[str, str | bool]:
    try:
        return UI_CONTRACTS[component_id].public_dict()
    except KeyError as error:
        raise KeyError(f"Component has no UI contract: {component_id}") from error
