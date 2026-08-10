from __future__ import annotations

import copy
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest
from dataset_audit_studio.app import component_catalog, component_schema_catalog
from dataset_audit_studio.app.component_catalog import build_component_registry
from dataset_audit_studio.app.component_schema_catalog import CONFIG_MODELS, UI_CONTRACTS
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.modular_exporting_coordinator import (
    build_exporting_component_plan,
)
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.core.component_registry import ComponentRegistry
from dataset_audit_studio.core.import_boundaries import find_component_import_violations

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "backend" / "dataset_audit_studio"


def test_component_import_boundaries_are_clean() -> None:
    assert find_component_import_violations(PACKAGE_ROOT) == ()


def test_builtin_registry_and_public_configuration_catalog_are_complete() -> None:
    registry = build_component_registry()
    component_ids = {definition.manifest.id for definition in registry.definitions}
    assert len(component_ids) == 14
    assert component_ids == set(CONFIG_MODELS) == set(UI_CONTRACTS)
    legacy_prefix = "caption" + "."
    assert not [
        component_id for component_id in component_ids if component_id.startswith(legacy_prefix)
    ]


def test_copy_task_config_has_no_legacy_exporting_component_plan() -> None:
    registry = build_component_registry()
    task_config = ComponentTaskConfigMaterializer(registry).materialize(
        materialize_profile("general")["components"],
        profile="general",
        require_profile=True,
    )
    resolved = registry.resolve_task_config(task_config)
    by_id = {item.definition.manifest.id: item for item in resolved}
    assert by_id["export.dataset"].dependency_ids == ()
    assert "latent.resolve" not in by_id
    assert build_exporting_component_plan(task_config, registry=registry) == ()


def test_rewrite_component_plan_contains_only_the_rewrite_executor() -> None:
    registry = build_component_registry()
    components = copy.deepcopy(materialize_profile("general")["components"])
    components["export.dataset"]["config"]["mode"] = "rewrite"
    task_config = ComponentTaskConfigMaterializer(registry).materialize(
        components,
        profile="general",
        require_profile=True,
    )

    plan = build_exporting_component_plan(task_config, registry=registry)

    assert tuple(item.component_id for item in plan) == ("export.dataset",)


def _registration_module():
    return import_module("dataset_audit_studio.app.component_registration")


def test_builtin_registration_catalog_derives_all_component_catalogs() -> None:
    registration = _registration_module()
    catalog = registration.BuiltinComponentRegistrationCatalog(
        registration.BUILTIN_COMPONENT_REGISTRATIONS
    )

    assert catalog.component_ids == tuple(
        definition.manifest.id for definition in build_component_registry().definitions
    )
    assert len(catalog.component_ids) == 14
    assert set(catalog.component_ids) == set(catalog.config_models)
    assert set(catalog.component_ids) == set(catalog.ui_contracts)
    assert set(catalog.component_ids) == set(catalog.phase_by_component)
    assert set(catalog.component_ids) == set(catalog.execution_bindings)


def test_component_schema_catalog_is_a_registration_derived_facade() -> None:
    registration = _registration_module()
    catalog = registration.BUILTIN_COMPONENT_REGISTRATION_CATALOG

    assert component_schema_catalog.EmptyComponentConfig is registration.EmptyComponentConfig
    assert component_schema_catalog.ComponentUIContract is registration.ComponentUIContract
    assert component_schema_catalog.CONFIG_MODELS is catalog.config_models
    assert component_schema_catalog.UI_CONTRACTS is catalog.ui_contracts


def test_component_catalog_derives_definitions_and_phase_map_from_registration() -> None:
    registration = _registration_module()
    catalog = registration.BUILTIN_COMPONENT_REGISTRATION_CATALOG

    assert component_catalog.BUILTIN_COMPONENTS is catalog.definitions
    assert component_catalog.component_phase_map() == dict(catalog.phase_by_component)


def test_component_phase_map_rejects_registration_coverage_drift() -> None:
    registration = _registration_module()
    catalog = registration.BUILTIN_COMPONENT_REGISTRATION_CATALOG
    partial_registry = ComponentRegistry(
        catalog.definitions[:-1],
        external_capabilities=("source.dataset.v1",),
    )

    with pytest.raises(
        registration.ComponentRegistrationError,
        match=r"Registration coverage mismatch; missing=\['export.dataset'\], unknown=\[\]",
    ):
        component_catalog.component_phase_map(partial_registry)


def test_builtin_registration_catalog_rejects_duplicate_component_ids() -> None:
    registration = _registration_module()
    first = registration.BUILTIN_COMPONENT_REGISTRATIONS[0]

    with pytest.raises(
        registration.ComponentRegistrationError,
        match=r"Duplicate built-in component registration ids: \['media.scan'\]",
    ):
        registration.BuiltinComponentRegistrationCatalog((first, first))


def test_builtin_registration_catalog_rejects_missing_config_model() -> None:
    registration = _registration_module()
    first = registration.BUILTIN_COMPONENT_REGISTRATIONS[0]
    missing_config = replace(first, config_model=None)

    with pytest.raises(
        registration.ComponentRegistrationError,
        match="Component registration media.scan has no config model",
    ):
        registration.BuiltinComponentRegistrationCatalog(
            (missing_config, *registration.BUILTIN_COMPONENT_REGISTRATIONS[1:])
        )


def test_builtin_registration_catalog_rejects_missing_ui_contract() -> None:
    registration = _registration_module()
    first = registration.BUILTIN_COMPONENT_REGISTRATIONS[0]
    missing_ui = replace(first, ui_contract=None)

    with pytest.raises(
        registration.ComponentRegistrationError,
        match="Component registration media.scan has no UI contract",
    ):
        registration.BuiltinComponentRegistrationCatalog(
            (missing_ui, *registration.BUILTIN_COMPONENT_REGISTRATIONS[1:])
        )


def test_builtin_registration_catalog_rejects_missing_execution_binding() -> None:
    registration = _registration_module()
    first = registration.BUILTIN_COMPONENT_REGISTRATIONS[0]
    missing_binding = replace(first, execution_binding=None)

    with pytest.raises(
        registration.ComponentRegistrationError,
        match="Component registration media.scan has no execution binding",
    ):
        registration.BuiltinComponentRegistrationCatalog(
            (missing_binding, *registration.BUILTIN_COMPONENT_REGISTRATIONS[1:])
        )


def test_builtin_registration_catalog_rejects_unknown_execution_binding() -> None:
    registration = _registration_module()
    first = registration.BUILTIN_COMPONENT_REGISTRATIONS[0]
    unknown_binding = replace(first, execution_binding=object())

    with pytest.raises(
        registration.ComponentRegistrationError,
        match="Component registration media.scan has an unknown execution binding",
    ):
        registration.BuiltinComponentRegistrationCatalog(
            (unknown_binding, *registration.BUILTIN_COMPONENT_REGISTRATIONS[1:])
        )


def test_builtin_registration_catalog_rejects_unknown_registration_lookup() -> None:
    registration = _registration_module()
    catalog = registration.BuiltinComponentRegistrationCatalog(
        registration.BUILTIN_COMPONENT_REGISTRATIONS
    )

    with pytest.raises(
        registration.ComponentRegistrationError,
        match="Unknown built-in component registration: not.registered",
    ):
        catalog.registration_for("not.registered")


def test_builtin_registration_catalog_rejects_component_set_drift() -> None:
    registration = _registration_module()
    catalog = registration.BuiltinComponentRegistrationCatalog(
        registration.BUILTIN_COMPONENT_REGISTRATIONS
    )

    with pytest.raises(
        registration.ComponentRegistrationError,
        match=(
            r"Registration coverage mismatch; missing=\['media.scan'\], "
            r"unknown=\['not.registered'\]"
        ),
    ):
        catalog.validate_component_ids(
            (*catalog.component_ids[1:], "not.registered")
        )
