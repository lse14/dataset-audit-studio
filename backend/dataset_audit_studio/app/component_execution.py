from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from functools import partial
from pathlib import Path
from typing import Any

from dataset_audit_studio.app.component_catalog import COMPONENT_REGISTRY
from dataset_audit_studio.app.component_registration import (
    BUILTIN_COMPONENT_REGISTRATION_CATALOG,
    BuiltinComponentRegistrationCatalog,
    BuiltinExecutionBinding,
    ClusteringExecutionBinding,
    ComponentRegistrationError,
    DatasetExportExecutionBinding,
    LatentResolutionExecutionBinding,
    MediaScanExecutionBinding,
    ReviewDecisionExecutionBinding,
    ScoringExecutionBinding,
    StyleExecutionBinding,
    TechnicalMetricsExecutionBinding,
)
from dataset_audit_studio.app.duplicate_evidence import DuplicateEvidenceProducer
from dataset_audit_studio.app.modular_clustering import (
    ModularClusteringComponentService,
    finalize_modular_clustering,
)
from dataset_audit_studio.app.modular_clustering_coordinator import (
    ClusteringComponentPlan,
    ModularClusteringCoordinator,
)
from dataset_audit_studio.app.modular_exporting import finalize_modular_exporting
from dataset_audit_studio.app.modular_exporting_coordinator import (
    ExportingComponentPlan,
    ModularExportingCoordinator,
)
from dataset_audit_studio.app.modular_scoring import (
    ModularScoringComponentService,
    finalize_modular_scoring,
)
from dataset_audit_studio.app.modular_scoring_coordinator import (
    ModularScoringCoordinator,
    ScoringComponentPlan,
)
from dataset_audit_studio.app.style_analysis import StyleAnalyzer
from dataset_audit_studio.app.style_process import run_style_subprocess
from dataset_audit_studio.core.component_contracts import (
    ComponentBatchResult,
    ComponentExecutionPort,
    ComponentRunRequest,
)
from dataset_audit_studio.core.component_registry import ComponentRegistry
from dataset_audit_studio.core.model_assets import RuntimeAssets
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.export.service import DatasetExporter
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.model_adapters.service import ModelService
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scanner.service import DatasetScanner
from dataset_audit_studio.scoring.coordinator import wait_for_model_ids

PhaseFinalizer = Callable[[WorkerToken, tuple[str, ...]], str]
ExecutionCallable = Callable[[ComponentRunRequest, WorkerToken], Any]


@dataclass(frozen=True)
class ComponentExecutionDependencies:
    database: Database
    tasks: TaskService
    model_service: ModelService | None
    project_root: Path = PROJECT_ROOT
    poll_seconds: float = 0.5
    subprocess: bool = True


class ComponentExecutionCatalog:
    def __init__(
        self,
        registry: ComponentRegistry,
        ports: Mapping[str, ComponentExecutionPort],
        *,
        finalizers: Mapping[str, PhaseFinalizer] | None = None,
        registration_catalog: BuiltinComponentRegistrationCatalog = (
            BUILTIN_COMPONENT_REGISTRATION_CATALOG
        ),
    ) -> None:
        registered = {item.manifest.id for item in registry.definitions}
        supplied = set(ports)
        if registered != supplied:
            missing = sorted(registered - supplied)
            unknown = sorted(supplied - registered)
            raise ValueError(
                f"Execution port coverage mismatch; missing={missing}, unknown={unknown}"
            )
        self.registry = registry
        self.registration_catalog = registration_catalog
        self._ports = dict(ports)
        self._finalizers = dict(finalizers or {})

    @property
    def component_ids(self) -> tuple[str, ...]:
        return tuple(item.manifest.id for item in self.registry.definitions)

    def port(self, component_id: str) -> ComponentExecutionPort:
        try:
            return self._ports[component_id]
        except KeyError as error:
            raise KeyError(f"Component has no execution port: {component_id}") from error

    def execute(self, request: ComponentRunRequest) -> ComponentBatchResult:
        return self.port(request.component_id).execute(request)

    def finalize_phase(
        self,
        phase: str,
        token: WorkerToken,
        component_order: tuple[str, ...],
    ) -> str | None:
        finalizer = self._finalizers.get(phase)
        return None if finalizer is None else finalizer(token, component_order)

    def replace_port(
        self,
        component_id: str,
        port: ComponentExecutionPort,
    ) -> ComponentExecutionCatalog:
        if component_id not in self._ports:
            raise KeyError(f"Unknown component: {component_id}")
        replaced = dict(self._ports)
        replaced[component_id] = port
        return ComponentExecutionCatalog(
            self.registry,
            replaced,
            finalizers=self._finalizers,
            registration_catalog=self.registration_catalog,
        )


class CallableComponentExecutionPort:
    def __init__(
        self,
        tasks: TaskService,
        *,
        component_id: str,
        task_phase: str,
        execute: ExecutionCallable,
    ) -> None:
        self.tasks = tasks
        self.component_id = component_id
        self.task_phase = task_phase
        self._execute = execute

    def execute(self, request: ComponentRunRequest) -> ComponentBatchResult:
        if request.component_id != self.component_id:
            raise ValueError(f"Execution port {self.component_id} received {request.component_id}")
        token = WorkerToken(
            request.task_id,
            request.worker_owner,
            request.execution_epoch,
        )
        summary = self._execute(request, token)
        return _component_result(
            self.tasks,
            request,
            self.task_phase,
            summary,
        )


def build_component_execution_catalog(
    dependencies: ComponentExecutionDependencies,
    *,
    registry: ComponentRegistry = COMPONENT_REGISTRY,
    registration_catalog: BuiltinComponentRegistrationCatalog = (
        BUILTIN_COMPONENT_REGISTRATION_CATALOG
    ),
) -> ComponentExecutionCatalog:
    component_ids = tuple(definition.manifest.id for definition in registry.definitions)
    registration_catalog.validate_component_ids(component_ids)
    tasks = dependencies.tasks
    project_root = dependencies.project_root.resolve(strict=False)
    scanner = DatasetScanner(tasks, project_root=project_root)
    duplicate_evidence = DuplicateEvidenceProducer(dependencies.database)

    scoring = ModularScoringCoordinator(
        dependencies.database,
        tasks,
        model_service=dependencies.model_service,
        registry=registry,
        process_runner=(None if dependencies.subprocess else _run_scoring_component_inline)
        or _default_scoring_process_runner(),
        project_root=project_root,
        poll_seconds=dependencies.poll_seconds,
    )
    clustering = ModularClusteringCoordinator(
        dependencies.database,
        tasks,
        model_service=dependencies.model_service,
        registry=registry,
        process_runner=(None if dependencies.subprocess else _run_clustering_component_inline)
        or _default_clustering_process_runner(),
        project_root=project_root,
        poll_seconds=dependencies.poll_seconds,
    )
    exporting_process_runner = (
        _default_exporting_process_runner()
        if dependencies.subprocess
        else partial(
            _run_exporting_component_inline,
            registration_catalog=registration_catalog,
        )
    )
    exporting = ModularExportingCoordinator(
        dependencies.database,
        tasks,
        model_service=dependencies.model_service,
        registry=registry,
        process_runner=exporting_process_runner,
        project_root=project_root,
        poll_seconds=dependencies.poll_seconds,
    )

    def scoring_component(request: ComponentRunRequest, token: WorkerToken):
        return scoring.run_component(
            token,
            ScoringComponentPlan(request.component_id, request.runtime_model_ids),
            component_order=request.component_order,
        )

    def clustering_component(request: ComponentRunRequest, token: WorkerToken):
        return clustering.run_component(
            token,
            ClusteringComponentPlan(request.component_id, request.runtime_model_ids),
            component_order=request.component_order,
        )

    def exporting_component(request: ComponentRunRequest, token: WorkerToken):
        return exporting.run_component(
            token,
            ExportingComponentPlan(request.component_id, request.runtime_model_ids),
        )

    def style_component(request: ComponentRunRequest, token: WorkerToken):
        assets = _wait_for_assets(
            dependencies,
            token,
            request.runtime_model_ids,
            phase=TaskStatus.STYLE_ANALYSIS,
        )
        if assets is None:
            return None
        if dependencies.subprocess:
            return run_style_subprocess(
                dependencies.database,
                tasks,
                token,
                assets,
                project_root=project_root,
                poll_seconds=dependencies.poll_seconds,
            )
        return StyleAnalyzer(tasks, project_root=project_root).run(token, assets)

    def media_scan_component(_request: ComponentRunRequest, token: WorkerToken):
        return scanner.run_scanning(token)

    def technical_metrics_component(_request: ComponentRunRequest, token: WorkerToken):
        duplicate_evidence.produce(token.task_id, tasks.get_task(token.task_id).config_hash)
        return scanner.finalize_precomputed_cpu_metrics(token)

    def review_decision_component(_request: ComponentRunRequest, _token: WorkerToken):
        return {"component_complete": True}

    def callable_for_binding(binding: BuiltinExecutionBinding) -> ExecutionCallable:
        if isinstance(binding, MediaScanExecutionBinding):
            return media_scan_component
        if isinstance(binding, TechnicalMetricsExecutionBinding):
            return technical_metrics_component
        if isinstance(binding, ScoringExecutionBinding):
            return scoring_component
        if isinstance(binding, StyleExecutionBinding):
            return style_component
        if isinstance(binding, ClusteringExecutionBinding):
            return clustering_component
        if isinstance(binding, ReviewDecisionExecutionBinding):
            return review_decision_component
        if isinstance(binding, LatentResolutionExecutionBinding):
            return _reject_legacy_exporting_component
        if isinstance(binding, DatasetExportExecutionBinding):
            return exporting_component
        raise ValueError(f"Unsupported execution binding: {type(binding).__name__}")

    ports = {}
    for definition in registry.definitions:
        component_id = definition.manifest.id
        registration = registration_catalog.registration_for(component_id)
        ports[component_id] = CallableComponentExecutionPort(
            tasks,
            component_id=component_id,
            task_phase=definition.manifest.task_phase,
            execute=callable_for_binding(registration.execution_binding),
        )
    finalizers: dict[str, PhaseFinalizer] = {
        TaskStatus.MODEL_SCORING.value: lambda token, order: finalize_modular_scoring(
            tasks,
            token,
            component_order=order,
        ),
        TaskStatus.STYLE_ANALYSIS.value: lambda token, _order: (
            tasks.complete_phase(
                token,
                phase=TaskStatus.STYLE_ANALYSIS,
            ).status
        ),
        TaskStatus.SEMANTIC_CLUSTERING.value: (
            lambda token, order: finalize_modular_clustering(
                tasks,
                token,
                component_order=order,
            )
        ),
        TaskStatus.EXPORTING.value: lambda token, order: finalize_modular_exporting(
            tasks,
            token,
            component_order=order,
        ),
    }
    return ComponentExecutionCatalog(
        registry,
        ports,
        finalizers=finalizers,
        registration_catalog=registration_catalog,
    )


def _component_result(
    tasks: TaskService,
    request: ComponentRunRequest,
    task_phase: str,
    summary: Any,
) -> ComponentBatchResult:
    payload = _summary_payload(summary)
    current = tasks.get_task(request.task_id)
    complete_value = payload.get("component_complete")
    if isinstance(complete_value, bool):
        complete = complete_value
    else:
        complete = current.status != task_phase and current.status not in {
            TaskStatus.PAUSED.value,
            TaskStatus.TERMINATED.value,
            TaskStatus.FAILED.value,
        }
    completed_items = 0
    for key in (
        "completed_items",
        "processed_samples",
        "processed",
        "exported_images",
        "eligible_samples",
        "datasets",
    ):
        value = payload.get(key)
        if isinstance(value, int) and value >= 0:
            completed_items = value
            break
    return ComponentBatchResult(
        component_id=request.component_id,
        batch_index=0,
        completed_items=completed_items,
        component_complete=complete,
        final_status=current.status,
        next_cursor=payload,
    )


def _summary_payload(summary: Any) -> dict[str, Any]:
    if summary is None:
        return {"component_complete": False}
    if isinstance(summary, Mapping):
        return dict(summary)
    if is_dataclass(summary) and not isinstance(summary, type):
        return asdict(summary)
    return {"result": summary}


def _wait_for_assets(
    dependencies: ComponentExecutionDependencies,
    token: WorkerToken,
    model_ids: tuple[str, ...],
    *,
    phase: TaskStatus,
) -> RuntimeAssets | None:
    if dependencies.model_service is None:
        if model_ids:
            raise RuntimeError(f"Component in {phase.value} requires the model service")
        return RuntimeAssets(
            models_root=str((dependencies.project_root / "models").resolve(strict=False)),
            models=(),
        )
    return wait_for_model_ids(
        dependencies.model_service,
        dependencies.tasks,
        token,
        model_ids,
        phase=phase,
        poll_seconds=dependencies.poll_seconds,
    )


def _run_scoring_component_inline(
    _database,
    tasks,
    token,
    assets,
    *,
    component_id,
    component_order,
    project_root,
    **_kwargs,
) -> dict[str, Any]:
    return asdict(
        ModularScoringComponentService(tasks, project_root=project_root).run(
            token,
            assets,
            component_id=component_id,
            component_order=component_order,
        )
    )


def _run_clustering_component_inline(
    _database,
    tasks,
    token,
    assets,
    *,
    component_id,
    component_order,
    project_root,
    **_kwargs,
) -> dict[str, Any]:
    return asdict(
        ModularClusteringComponentService(tasks, project_root=project_root).run(
            token,
            assets,
            component_id=component_id,
            component_order=component_order,
        )
    )


def _run_exporting_component_inline(
    _database,
    tasks,
    token,
    assets,
    *,
    component_id,
    project_root,
    registration_catalog: BuiltinComponentRegistrationCatalog = (
        BUILTIN_COMPONENT_REGISTRATION_CATALOG
    ),
    **_kwargs,
) -> dict[str, Any]:
    try:
        binding = registration_catalog.registration_for(component_id).execution_binding
    except ComponentRegistrationError as error:
        raise ValueError(f"Unsupported exporting component: {component_id}") from error
    if isinstance(binding, DatasetExportExecutionBinding):
        summary = DatasetExporter(
            tasks,
            project_root=project_root,
        ).run(token, finalize_phase=False)
    else:
        raise ValueError(f"Unsupported exporting component: {component_id}")
    return {**asdict(summary), "component_id": component_id}


def _reject_legacy_exporting_component(
    request,
    _token,
    *,
    component_id: str | None = None,
    **_kwargs,
) -> dict[str, Any]:
    component_id = component_id or getattr(request, "component_id", "legacy")
    raise ValueError(
        f"Unsupported exporting component: {component_id}; legacy latent worker is disabled"
    )


def _default_scoring_process_runner():
    from dataset_audit_studio.app.modular_scoring_process import (
        run_modular_scoring_component_subprocess,
    )

    return run_modular_scoring_component_subprocess


def _default_clustering_process_runner():
    from dataset_audit_studio.app.modular_clustering_process import (
        run_modular_clustering_component_subprocess,
    )

    return run_modular_clustering_component_subprocess


def _default_exporting_process_runner():
    from dataset_audit_studio.app.modular_exporting_process import (
        run_modular_exporting_component_subprocess,
    )

    return run_modular_exporting_component_subprocess
