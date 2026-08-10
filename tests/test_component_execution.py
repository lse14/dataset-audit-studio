from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from dataset_audit_studio.app import component_execution
from dataset_audit_studio.app.component_catalog import build_component_registry
from dataset_audit_studio.app.component_execution import (
    ComponentExecutionCatalog,
    ComponentExecutionDependencies,
    build_component_execution_catalog,
)
from dataset_audit_studio.app.component_registration import (
    BUILTIN_COMPONENT_REGISTRATION_CATALOG,
    TechnicalMetricsExecutionBinding,
)
from dataset_audit_studio.core.component_contracts import ComponentBatchResult, ComponentRunRequest
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken


class _Port:
    def __init__(self, component_id: str) -> None:
        self.component_id = component_id

    def execute(self, request: ComponentRunRequest) -> ComponentBatchResult:
        assert request.component_id == self.component_id
        return ComponentBatchResult(
            component_id=self.component_id,
            batch_index=0,
            completed_items=0,
            component_complete=True,
            final_status="exporting",
        )


class _RecordingRegistrationCatalog:
    def __init__(self, *, replacements: dict[str, object] | None = None) -> None:
        self.replacements = replacements or {}
        self.validated_ids: tuple[str, ...] | None = None
        self.lookups: list[str] = []

    def validate_component_ids(self, component_ids: tuple[str, ...]) -> None:
        self.validated_ids = tuple(component_ids)
        BUILTIN_COMPONENT_REGISTRATION_CATALOG.validate_component_ids(component_ids)

    def registration_for(self, component_id: str):
        self.lookups.append(component_id)
        registration = BUILTIN_COMPONENT_REGISTRATION_CATALOG.registration_for(component_id)
        return self.replacements.get(component_id, registration)


@dataclass(frozen=True)
class _InlineSummary:
    path: str


def test_execution_factory_uses_registration_bindings_in_registry_order(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    registry = build_component_registry()
    media_scan = BUILTIN_COMPONENT_REGISTRATION_CATALOG.registration_for("media.scan")
    registration_catalog = _RecordingRegistrationCatalog(
        replacements={
            "media.scan": replace(
                media_scan,
                execution_binding=TechnicalMetricsExecutionBinding(),
            )
        }
    )

    catalog = build_component_execution_catalog(
        ComponentExecutionDependencies(
            database=database,
            tasks=task_service,
            model_service=None,
            project_root=tmp_path,
            subprocess=False,
        ),
        registry=registry,
        registration_catalog=registration_catalog,
    )

    component_ids = tuple(definition.manifest.id for definition in registry.definitions)
    assert registration_catalog.validated_ids == component_ids
    assert registration_catalog.lookups == list(component_ids)
    assert catalog.port("media.scan")._execute is catalog.port("metrics.technical")._execute


def test_inline_exporting_runs_the_rewrite_component_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, bool | None]] = []

    class _Exporter:
        def __init__(self, _tasks, *, project_root: Path) -> None:
            assert project_root == tmp_path

        def run(self, _token: WorkerToken, *, finalize_phase: bool) -> _InlineSummary:
            calls.append(("export", finalize_phase))
            return _InlineSummary("export")

    monkeypatch.setattr(component_execution, "DatasetExporter", _Exporter)
    registration_catalog = _RecordingRegistrationCatalog()
    token = WorkerToken("task", "worker", 1)

    exported = component_execution._run_exporting_component_inline(
        None,
        object(),
        token,
        None,
        component_id="export.dataset",
        project_root=tmp_path,
        registration_catalog=registration_catalog,
    )

    assert exported == {"path": "export", "component_id": "export.dataset"}
    assert calls == [("export", False)]
    assert registration_catalog.lookups == ["export.dataset"]


def test_execution_catalog_retains_registration_source_after_port_replacement() -> None:
    registry = build_component_registry()
    ports = {
        definition.manifest.id: _Port(definition.manifest.id)
        for definition in registry.definitions
    }
    catalog = ComponentExecutionCatalog(
        registry,
        ports,
        registration_catalog=BUILTIN_COMPONENT_REGISTRATION_CATALOG,
    )

    updated = catalog.replace_port("export.dataset", _Port("export.dataset"))

    assert catalog.registration_catalog is BUILTIN_COMPONENT_REGISTRATION_CATALOG
    assert updated.registration_catalog is BUILTIN_COMPONENT_REGISTRATION_CATALOG


def test_execution_factory_binds_custom_registration_to_inline_exporting(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_runners = []
    invoked_with: list[object | None] = []

    class _CapturingExportingCoordinator:
        def __init__(self, *_args, process_runner, **_kwargs) -> None:
            captured_runners.append(process_runner)

        def run_component(self, *_args, **_kwargs):
            raise AssertionError("The exporting port must not run in this contract")

    def _inline_exporting(*_args, **kwargs) -> dict[str, str]:
        invoked_with.append(kwargs.get("registration_catalog"))
        return {"component_id": kwargs["component_id"]}

    monkeypatch.setattr(
        component_execution,
        "ModularExportingCoordinator",
        _CapturingExportingCoordinator,
    )
    monkeypatch.setattr(
        component_execution,
        "_run_exporting_component_inline",
        _inline_exporting,
    )
    registration_catalog = _RecordingRegistrationCatalog()
    catalog = build_component_execution_catalog(
        ComponentExecutionDependencies(
            database=database,
            tasks=task_service,
            model_service=None,
            project_root=tmp_path,
            subprocess=False,
        ),
        registration_catalog=registration_catalog,
    )

    with pytest.raises(ValueError, match="latent.resolve"):
        catalog.execute(
            ComponentRunRequest(
                task_id="task",
                component_id="latent.resolve",
                worker_owner="worker",
                execution_epoch=1,
                normalized_config={},
            )
        )

    assert invoked_with == []


def test_execution_catalog_covers_and_replaces_each_registered_component_port() -> None:
    registry = build_component_registry()
    catalog = ComponentExecutionCatalog(
        registry,
        {
            definition.manifest.id: _Port(definition.manifest.id)
            for definition in registry.definitions
        },
    )
    replacement = _Port("export.dataset")
    updated = catalog.replace_port("export.dataset", replacement)
    assert updated.port("export.dataset") is replacement
    assert updated.component_ids == tuple(item.manifest.id for item in registry.definitions)
