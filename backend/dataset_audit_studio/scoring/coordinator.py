from __future__ import annotations

import time
from contextlib import suppress

from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.jobs.errors import StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.model_adapters.errors import ModelOperationConflict
from dataset_audit_studio.model_adapters.service import ModelService
from dataset_audit_studio.scoring.assets import (
    requested_model_ids,
    resolve_runtime_assets,
)
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.errors import ModelAssetDownloadError
from dataset_audit_studio.scoring.types import RuntimeAssets


def wait_for_runtime_assets(
    models: ModelService,
    tasks: TaskService,
    token: WorkerToken,
    config: ScoringConfig,
    *,
    poll_seconds: float = 0.5,
    heartbeat_seconds: float = 30.0,
) -> RuntimeAssets | None:
    requested = requested_model_ids(config)
    return wait_for_model_ids(
        models,
        tasks,
        token,
        requested,
        phase=TaskStatus.MODEL_SCORING,
        poll_seconds=poll_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )


def wait_for_model_ids(
    models: ModelService,
    tasks: TaskService,
    token: WorkerToken,
    requested: tuple[str, ...],
    *,
    phase: TaskStatus,
    poll_seconds: float = 0.5,
    heartbeat_seconds: float = 30.0,
) -> RuntimeAssets | None:
    if not requested:
        return RuntimeAssets(
            models_root=str(models.storage.models_root.resolve(strict=True)),
            models=(),
        )
    for model_id in requested:
        status = models.get_model(model_id)
        if status.runtime_ready:
            continue
        with suppress(ModelOperationConflict):
            models.download(model_id, include_dependencies=True)

    last_heartbeat = time.monotonic()
    while True:
        current = tasks.get_task(token.task_id)
        if current.status in {
            TaskStatus.PAUSING.value,
            TaskStatus.TERMINATING.value,
        }:
            _commit_asset_wait_control(tasks, token, requested, phase=phase)
            return None
        if current.status in {
            TaskStatus.PAUSED.value,
            TaskStatus.TERMINATED.value,
            TaskStatus.FAILED.value,
        }:
            return None

        statuses = [models.get_model(model_id) for model_id in requested]
        if all(status.runtime_ready for status in statuses):
            break
        failures = [
            status
            for status in statuses
            if status.installation_status in {"canceled", "corrupt", "failed"}
        ]
        if failures:
            detail = "; ".join(
                f"{status.id}: {status.installation_status}: {status.error or 'no detail'}"
                for status in failures
            )
            raise ModelAssetDownloadError(f"Required model installation failed: {detail}")
        if time.monotonic() - last_heartbeat >= heartbeat_seconds:
            try:
                tasks.heartbeat(token, lease_seconds=300)
            except StaleWorkerToken:
                return None
            last_heartbeat = time.monotonic()
        time.sleep(poll_seconds)

    return resolve_runtime_assets(models, requested)


def _commit_asset_wait_control(
    tasks: TaskService,
    token: WorkerToken,
    requested: tuple[str, ...],
    *,
    phase: TaskStatus,
) -> None:
    task = tasks.get_task(token.task_id)
    checkpoints = [
        checkpoint
        for checkpoint in tasks.list_checkpoints(
            task.id, phase=phase.value
        )
        if checkpoint.config_hash == task.config_hash
    ]
    batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
    completed = checkpoints[-1].completed_items if checkpoints else 0
    tasks.commit_batch(
        token,
        phase=phase,
        config_hash=task.config_hash,
        batch_index=batch_index,
        completed_items=completed,
        progress_total=max(completed, task.progress_total or 0),
        cursor={
            "asset_wait": True,
            "requested_models": list(requested),
            "next_index": completed,
        },
        lease_seconds=300,
    )
