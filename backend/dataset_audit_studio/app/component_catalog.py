from __future__ import annotations

from dataset_audit_studio.app.component_registration import (
    BUILTIN_COMPONENT_REGISTRATION_CATALOG,
    BuiltinComponentRegistrationCatalog,
)
from dataset_audit_studio.core.component_registry import ComponentRegistry

BUILTIN_COMPONENTS = BUILTIN_COMPONENT_REGISTRATION_CATALOG.definitions


def build_component_registry(
    registration_catalog: BuiltinComponentRegistrationCatalog = (
        BUILTIN_COMPONENT_REGISTRATION_CATALOG
    ),
) -> ComponentRegistry:
    return ComponentRegistry(
        registration_catalog.definitions,
        external_capabilities=("source.dataset.v1",),
    )


COMPONENT_REGISTRY = build_component_registry()


def component_phase_map(
    registry: ComponentRegistry = COMPONENT_REGISTRY,
    *,
    registration_catalog: BuiltinComponentRegistrationCatalog = (
        BUILTIN_COMPONENT_REGISTRATION_CATALOG
    ),
) -> dict[str, str]:
    component_ids = tuple(definition.manifest.id for definition in registry.definitions)
    registration_catalog.validate_component_ids(component_ids)
    return {
        component_id: registration_catalog.phase_by_component[component_id]
        for component_id in component_ids
    }
