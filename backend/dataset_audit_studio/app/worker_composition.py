from __future__ import annotations

from dataset_audit_studio.app.component_execution import (
    ComponentExecutionDependencies,
    build_component_execution_catalog,
)
from dataset_audit_studio.app.registry_task_runner import RegistryTaskRunner
from dataset_audit_studio.jobs.runner import WorkerRuntimeContext


def build_registry_task_runner(context: WorkerRuntimeContext) -> RegistryTaskRunner:
    execution = build_component_execution_catalog(
        ComponentExecutionDependencies(
            database=context.database,
            tasks=context.tasks,
            model_service=context.model_service,
            project_root=context.project_root,
            poll_seconds=context.poll_seconds,
            subprocess=context.use_subprocess,
        )
    )
    return RegistryTaskRunner(context.tasks, execution)
