from __future__ import annotations

from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.jobs.errors import InvalidTaskTransition, StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken

MODULAR_EXPORTING_COMPONENT_IDS = frozenset(
    {
        "export.dataset",
    }
)


def finalize_modular_exporting(
    tasks: TaskService,
    token: WorkerToken,
    *,
    component_order: tuple[str, ...],
) -> str:
    task = tasks.get_task(token.task_id)
    checkpoints = [
        checkpoint
        for checkpoint in tasks.list_checkpoints(
            task.id,
            phase=TaskStatus.EXPORTING.value,
        )
        if checkpoint.config_hash == task.config_hash
    ]
    completed = {
        checkpoint.cursor.get("component_id")
        for checkpoint in checkpoints
        if checkpoint.cursor.get("modular_exporting") is True
        and checkpoint.cursor.get("component_complete") is True
    }
    missing = set(component_order) - completed
    if missing:
        raise RuntimeError(
            f"Cannot finalize incomplete exporting components: {sorted(missing)}"
        )
    current = tasks.get_task(task.id)
    if current.status in {TaskStatus.PAUSING.value, TaskStatus.TERMINATING.value}:
        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        return tasks.commit_batch(
            token,
            phase=TaskStatus.EXPORTING,
            config_hash=task.config_hash,
            batch_index=batch_index,
            completed_items=current.progress_current,
            progress_total=max(current.progress_current, current.progress_total or 0),
            cursor={
                "modular_exporting": True,
                "component_id": "exporting.finalize",
                "control_only": True,
            },
            lease_seconds=300,
        ).task.status
    try:
        return tasks.complete_phase(token, phase=TaskStatus.EXPORTING).status
    except StaleWorkerToken:
        return tasks.get_task(task.id).status
    except InvalidTaskTransition:
        current = tasks.get_task(task.id)
        if current.status not in {
            TaskStatus.PAUSING.value,
            TaskStatus.TERMINATING.value,
        }:
            raise
        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        return tasks.commit_batch(
            token,
            phase=TaskStatus.EXPORTING,
            config_hash=task.config_hash,
            batch_index=batch_index,
            completed_items=current.progress_current,
            progress_total=max(current.progress_current, current.progress_total or 0),
            cursor={
                "modular_exporting": True,
                "component_id": "exporting.finalize",
                "control_only": True,
            },
            lease_seconds=300,
        ).task.status
