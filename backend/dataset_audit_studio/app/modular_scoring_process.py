from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dataset_audit_studio.adapters.component_run_repository import ComponentRunRepository
from dataset_audit_studio.app.modular_scoring import ModularScoringComponentService
from dataset_audit_studio.core.model_assets import RuntimeAssets
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.phase_process import run_isolated_phase_subprocess
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT, assert_runtime_isolated


@dataclass(frozen=True)
class ModularScoringProcessPayload:
    component_id: str
    component_order: tuple[str, ...]
    assets: RuntimeAssets


def run_modular_scoring_component_subprocess(
    database: Database,
    tasks: TaskService,
    token: WorkerToken,
    assets: RuntimeAssets,
    *,
    component_id: str,
    component_order: tuple[str, ...],
    project_root: Path = PROJECT_ROOT,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    payload = ModularScoringProcessPayload(component_id, component_order, assets)
    return run_isolated_phase_subprocess(
        database,
        tasks,
        token,
        payload,
        phase_name=component_id.replace(".", "-"),
        entrypoint=_modular_scoring_process_entry,
        project_root=project_root,
        poll_seconds=poll_seconds,
    )


def _modular_scoring_process_entry(
    database_path: str,
    token: WorkerToken,
    payload: ModularScoringProcessPayload,
    project_root: str,
    result_queue,
) -> None:
    database: Database | None = None
    try:
        assert_runtime_isolated()
        database = Database(Path(database_path), enforce_project_boundary=False)
        summary = ModularScoringComponentService(
            TaskService(
                database,
                batch_checkpoint_writer=ComponentRunRepository.apply_batch_checkpoint,
            ),
            project_root=Path(project_root),
        ).run(
            token,
            payload.assets,
            component_id=payload.component_id,
            component_order=payload.component_order,
        )
        result_queue.put(
            {
                "ok": True,
                "summary": {
                    **summary.__dict__,
                    "process_pid": os.getpid(),
                    "runtime_model_ids": [
                        model.model_id for model in payload.assets.models
                    ],
                },
            }
        )
    except Exception as error:  # noqa: BLE001 - serialize the child failure to its owner
        result_queue.put(
            {
                "ok": False,
                "error_type": type(error).__name__,
                "error": str(error) or repr(error),
                "traceback": traceback.format_exc(limit=20)[-8000:],
            }
        )
    finally:
        if database is not None:
            database.dispose()
