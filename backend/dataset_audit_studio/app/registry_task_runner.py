from __future__ import annotations

from dataclasses import dataclass

from dataset_audit_studio.adapters.component_run_repository import (
    ComponentRunRepository,
)
from dataset_audit_studio.app.component_catalog import component_phase_map
from dataset_audit_studio.app.component_execution import ComponentExecutionCatalog
from dataset_audit_studio.core.component_contracts import ComponentRunRequest
from dataset_audit_studio.core.component_registry import ComponentRegistry
from dataset_audit_studio.database.enums import ComponentRunState, TaskStatus
from dataset_audit_studio.jobs.profile import require_builtin_profile
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import TaskView, WorkerToken


@dataclass(frozen=True)
class RegistryPhaseSummary:
    task_id: str
    phase: str
    component_ids: tuple[str, ...]
    final_status: str


class RegistryTaskRunner:
    def __init__(
        self,
        tasks: TaskService,
        execution: ComponentExecutionCatalog,
        *,
        registry: ComponentRegistry | None = None,
        runs: ComponentRunRepository | None = None,
    ) -> None:
        registry = execution.registry if registry is None else registry
        execution_ids = tuple(execution.component_ids)
        registry_ids = tuple(item.manifest.id for item in registry.definitions)
        execution.registration_catalog.validate_component_ids(execution_ids)
        execution.registration_catalog.validate_component_ids(registry_ids)
        if set(execution_ids) != set(registry_ids):
            raise ValueError("Execution catalog does not match the component registry")
        self.tasks = tasks
        self.execution = execution
        self.registry = registry
        self.runs = runs or ComponentRunRepository()

    def run(self, token: WorkerToken, task: TaskView) -> RegistryPhaseSummary:
        require_builtin_profile(task.config)
        phase = task.status
        phase_definitions = tuple(
            definition
            for definition in self.registry.definitions
            if definition.manifest.task_phase == phase
        )
        if not phase_definitions:
            raise RuntimeError(f"Worker phase has no registered components: {phase}")

        resolved = self.registry.resolve_task_config(task.config)
        active = tuple(
            item for item in resolved if item.definition.manifest.task_phase == phase
        )
        active_ids = tuple(item.definition.manifest.id for item in active)
        if not active:
            current = self.tasks.complete_phase(token, phase=phase)
            return RegistryPhaseSummary(task.id, phase, (), current.status)
        active_order = min(
            (item.definition.manifest.phase_order for item in active),
            default=min(item.manifest.phase_order for item in phase_definitions),
        )
        with self.tasks.database.write_session() as session:
            self.runs.sync_plan(
                session,
                task=task,
                resolved=resolved,
                phase_by_component=component_phase_map(
                    self.registry,
                    registration_catalog=self.execution.registration_catalog,
                ),
            )
            self.runs.mark_before_order_completed(
                session,
                task_id=task.id,
                config_hash=task.config_hash,
                phase_order=active_order,
            )

        for item in active:
            component_id = item.definition.manifest.id
            with self.tasks.database.write_session() as session:
                self.runs.mark_running(
                    session,
                    task_id=task.id,
                    config_hash=task.config_hash,
                    component_ids=(component_id,),
                )
            request = ComponentRunRequest(
                task_id=task.id,
                component_id=component_id,
                worker_owner=token.owner,
                execution_epoch=token.execution_epoch,
                normalized_config=item.config.model_dump(mode="json"),
                component_order=active_ids,
                runtime_model_ids=item.definition.model_ids(item.config),
            )
            try:
                result = self.execution.execute(request)
                if result.component_id != component_id:
                    raise RuntimeError(
                        f"Execution port returned {result.component_id} for {component_id}"
                    )
                current = self.tasks.get_task(task.id)
                if current.status == phase and not result.component_complete:
                    raise RuntimeError(f"Component {component_id} stopped incomplete")
            except Exception as error:
                self._record_failure(task, phase, component_id, error)
                raise

            self._record_result(
                task,
                phase,
                component_id,
                current,
                result.component_complete,
            )
            if current.status != phase:
                return RegistryPhaseSummary(task.id, phase, active_ids, current.status)

        self.execution.finalize_phase(phase, token, active_ids)
        current = self.tasks.get_task(task.id)
        with self.tasks.database.write_session() as session:
            self.runs.reconcile_phase_checkpoints(
                session,
                task_id=task.id,
                config_hash=task.config_hash,
                phase=phase,
            )
        return RegistryPhaseSummary(task.id, phase, active_ids, current.status)

    def _record_failure(
        self,
        task: TaskView,
        phase: str,
        component_id: str,
        error: Exception,
    ) -> None:
        with self.tasks.database.write_session() as session:
            self.runs.reconcile_phase_checkpoints(
                session,
                task_id=task.id,
                config_hash=task.config_hash,
                phase=phase,
            )
            self.runs.mark_status(
                session,
                task_id=task.id,
                config_hash=task.config_hash,
                component_ids=(component_id,),
                status=ComponentRunState.FAILED,
                error_code=type(error).__name__,
                error_message=str(error) or repr(error),
            )

    def _record_result(
        self,
        task: TaskView,
        phase: str,
        component_id: str,
        current: TaskView,
        component_complete: bool,
    ) -> None:
        if current.status == TaskStatus.PAUSED.value:
            status = ComponentRunState.PAUSED
        elif current.status == TaskStatus.TERMINATED.value:
            status = ComponentRunState.TERMINATED
        elif current.status == TaskStatus.FAILED.value:
            status = ComponentRunState.FAILED
        elif component_complete or current.status != phase:
            status = ComponentRunState.COMPLETED
        else:
            status = ComponentRunState.RUNNING
        with self.tasks.database.write_session() as session:
            self.runs.reconcile_phase_checkpoints(
                session,
                task_id=task.id,
                config_hash=task.config_hash,
                phase=phase,
            )
            self.runs.mark_status(
                session,
                task_id=task.id,
                config_hash=task.config_hash,
                component_ids=(component_id,),
                status=status,
            )
