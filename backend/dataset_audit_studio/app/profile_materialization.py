from __future__ import annotations

from typing import Any

from dataset_audit_studio.app.component_catalog import COMPONENT_REGISTRY
from dataset_audit_studio.app.component_registration import (
    BUILTIN_COMPONENT_REGISTRATION_CATALOG,
)
from dataset_audit_studio.core.component_registry import ComponentRegistry
from dataset_audit_studio.core.profile_contracts import (
    PROFILE_DEFAULT_DISABLED_COMPONENT_IDS,
    DatasetProfile,
    profile_constraints,
    resolve_dataset_profile,
)
from dataset_audit_studio.presets.builtin import apply_profile

__all__ = ("materialize_profile",)


def materialize_profile(
    profile: DatasetProfile | str,
    *,
    registry: ComponentRegistry | None = None,
) -> dict[str, Any]:
    selected_registry = registry or COMPONENT_REGISTRY
    components: dict[str, dict[str, Any]] = {}
    for definition in selected_registry.definitions:
        component_id = definition.manifest.id
        registration = BUILTIN_COMPONENT_REGISTRATION_CATALOG.registration_for(
            component_id
        )
        ui = registration.ui_contract
        components[component_id] = {
            "enabled": ui.activation == "required"
            or (ui.activation == "optional" and ui.recommended_enabled),
            "config": registration.config_model().model_dump(mode="json"),
        }
    resolved = resolve_dataset_profile(profile)
    components = apply_profile(components, resolved)
    constraints = profile_constraints(resolved)
    profile_enabled = {
        component_id
        for component_id, enabled in (
            ("embedding.semantic", constraints.semantic_enabled),
            ("cluster.hierarchy", constraints.hierarchy_enabled),
        )
        if enabled
    }
    for component_id in PROFILE_DEFAULT_DISABLED_COMPONENT_IDS:
        if component_id not in profile_enabled:
            components[component_id]["enabled"] = False
    return {
        "profile": resolved.value,
        "components": components,
    }
