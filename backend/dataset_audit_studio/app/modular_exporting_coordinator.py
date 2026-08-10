from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dataset_audit_studio.app.component_catalog import COMPONENT_REGISTRY
from dataset_audit_studio.app.modular_exporting import (
    MODULAR_EXPORTING_COMPONENT_IDS,
    finalize_modular_exporting,
)
from dataset_audit_studio.app.modular_exporting_process import (
    run_modular_exporting_component_subprocess,
)
from dataset_audit_studio.core.component_registry import ComponentRegistry
from dataset_audit_studio.core.model_assets import RuntimeAssets, select_runtime_assets
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.model_adapters.service import ModelService
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scoring.coordinator import wait_for_model_ids


@dataclass(frozen=True)
class ExportingComponentPlan:
    component_id: str
    model_ids: tuple[str, ...]


@dataclass(frozen=True)
class ModularExportingRunSummary:
    task_id: str
    component_summaries: tuple[dict[str, Any], ...]
    final_status: str


ProcessRunner = Callable[..., dict[str, Any]]
ComponentAssetWaiter = Callable[
    [WorkerToken, ExportingComponentPlan],
    RuntimeAssets | None,
]


def build_exporting_component_plan(
    task_config: dict[str, Any],
    *,
    registry: ComponentRegistry = COMPONENT_REGISTRY,
) -> tuple[ExportingComponentPlan, ...]:
    components = task_config.get("components")
    export = components.get("export.dataset") if isinstance(components, dict) else None
    export_config = export.get("config") if isinstance(export, dict) else None
    if not isinstance(export_config, dict) or export_config.get("mode") != "rewrite":
        return ()
    return tuple(
        ExportingComponentPlan(
            component_id=resolved.definition.manifest.id,
            model_ids=resolved.definition.model_ids(resolved.config),
        )
        for resolved in registry.resolve_task_config(task_config)
        if resolved.definition.manifest.id in MODULAR_EXPORTING_COMPONENT_IDS
    )


class ModularExportingCoordinator:
    def __init__(
        self,
        database: Database,
        tasks: TaskService,
        *,
        model_service: ModelService | None,
        registry: ComponentRegistry = COMPONENT_REGISTRY,
        process_runner: ProcessRunner = run_modular_exporting_component_subprocess,
        component_asset_waiter: ComponentAssetWaiter | None = None,
        project_root: Path = PROJECT_ROOT,
        poll_seconds: float = 0.5,
    ) -> None:
        self.database = database
        self.tasks = tasks
        self.model_service = model_service
        self.registry = registry
        self.process_runner = process_runner
        self.component_asset_waiter = component_asset_waiter
        self.project_root = project_root.resolve(strict=False)
        self.poll_seconds = poll_seconds

    def run(self, token: WorkerToken) -> ModularExportingRunSummary:
        task = self.tasks.get_task(token.task_id)
        plan = build_exporting_component_plan(task.config, registry=self.registry)
        component_order = tuple(item.component_id for item in plan)
        summaries: list[dict[str, Any]] = []
        for item in plan:
            summary = self.run_component(token, item)
            if summary is None:
                return self._summary(token.task_id, summaries)
            summaries.append(summary)
            current = self.tasks.get_task(token.task_id)
            if current.status != TaskStatus.EXPORTING.value:
                return self._summary(token.task_id, summaries)
        finalize_modular_exporting(
            self.tasks,
            token,
            component_order=component_order,
        )
        return self._summary(token.task_id, summaries)

    def run_component(
        self,
        token: WorkerToken,
        item: ExportingComponentPlan,
    ) -> dict[str, Any] | None:
        assets = self._wait_for_assets(token, item)
        if assets is None:
            return None
        summary = self.process_runner(
            self.database,
            self.tasks,
            token,
            assets,
            component_id=item.component_id,
            project_root=self.project_root,
            poll_seconds=self.poll_seconds,
        )
        if summary.get("component_id") != item.component_id:
            raise RuntimeError("Exporting subprocess returned the wrong component")
        current = self.tasks.get_task(token.task_id)
        if (
            current.status == TaskStatus.EXPORTING.value
            and summary.get("component_complete") is not True
        ):
            raise RuntimeError(f"Exporting component {item.component_id} is incomplete")
        return summary

    def _wait_for_assets(
        self,
        token: WorkerToken,
        item: ExportingComponentPlan,
    ) -> RuntimeAssets | None:
        if self.component_asset_waiter is not None:
            assets = self.component_asset_waiter(token, item)
            return None if assets is None else select_runtime_assets(assets, item.model_ids)
        if self.model_service is None:
            if item.model_ids:
                raise RuntimeError(
                    f"Exporting component {item.component_id} requires the model service"
                )
            return RuntimeAssets(
                models_root=str((self.database.path.parent / "models").resolve(strict=False)),
                models=(),
            )
        assets = wait_for_model_ids(
            self.model_service,
            self.tasks,
            token,
            item.model_ids,
            phase=TaskStatus.EXPORTING,
            poll_seconds=self.poll_seconds,
        )
        return None if assets is None else select_runtime_assets(assets, item.model_ids)

    def _summary(
        self,
        task_id: str,
        summaries: list[dict[str, Any]],
    ) -> ModularExportingRunSummary:
        return ModularExportingRunSummary(
            task_id=task_id,
            component_summaries=tuple(summaries),
            final_status=self.tasks.get_task(task_id).status,
        )
