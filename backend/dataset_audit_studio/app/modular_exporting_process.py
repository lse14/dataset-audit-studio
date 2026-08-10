from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dataset_audit_studio.adapters.component_run_repository import ComponentRunRepository
from dataset_audit_studio.core.model_assets import RuntimeAssets
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.export.service import DatasetExporter
from dataset_audit_studio.jobs.phase_process import run_isolated_phase_subprocess
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT, assert_runtime_isolated


@dataclass(frozen=True)
class ModularExportingProcessPayload:
    component_id: str
    assets: RuntimeAssets


def run_modular_exporting_component_subprocess(
    database: Database,
    tasks: TaskService,
    token: WorkerToken,
    assets: RuntimeAssets,
    *,
    component_id: str,
    project_root: Path = PROJECT_ROOT,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    payload = ModularExportingProcessPayload(component_id, assets)
    return run_isolated_phase_subprocess(
        database,
        tasks,
        token,
        payload,
        phase_name=component_id.replace(".", "-"),
        entrypoint=_modular_exporting_process_entry,
        project_root=project_root,
        poll_seconds=poll_seconds,
    )


def _modular_exporting_process_entry(
    database_path: str,
    token: WorkerToken,
    payload: ModularExportingProcessPayload,
    project_root: str,
    result_queue,
) -> None:
    database: Database | None = None
    try:
        assert_runtime_isolated()
        database = Database(Path(database_path), enforce_project_boundary=False)
        tasks = TaskService(
            database,
            batch_checkpoint_writer=ComponentRunRepository.apply_batch_checkpoint,
        )
        root = Path(project_root)
        if payload.component_id == "export.dataset":
            summary = DatasetExporter(tasks, project_root=root).run(
                token,
                finalize_phase=False,
            )
        else:
            raise ValueError(f"Unsupported modular exporting component: {payload.component_id}")
        result_queue.put(
            {
                "ok": True,
                "summary": {
                    **summary.__dict__,
                    "component_id": payload.component_id,
                    "process_pid": os.getpid(),
                    "runtime_model_ids": [
                        model.model_id for model in payload.assets.models
                    ],
                },
            }
        )
    except Exception as error:  # noqa: BLE001 - serialize child failure
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
