from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from dataset_audit_studio.adapters.component_run_repository import ComponentRunRepository
from dataset_audit_studio.app.style_analysis import StyleAnalyzer
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.phase_process import run_isolated_phase_subprocess
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT, assert_runtime_isolated
from dataset_audit_studio.scoring.types import RuntimeAssets
from dataset_audit_studio.style.repository import StyleRepository


def run_style_subprocess(
    database: Database,
    tasks: TaskService,
    token: WorkerToken,
    assets: RuntimeAssets,
    *,
    project_root: Path = PROJECT_ROOT,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    return run_isolated_phase_subprocess(
        database,
        tasks,
        token,
        assets,
        phase_name="style",
        entrypoint=_style_process_entry,
        project_root=project_root,
        poll_seconds=poll_seconds,
    )


def _style_process_entry(
    database_path: str,
    token: WorkerToken,
    assets: RuntimeAssets,
    project_root: str,
    result_queue,
) -> None:
    database: Database | None = None
    try:
        assert_runtime_isolated()
        database = Database(Path(database_path), enforce_project_boundary=False)
        summary = StyleAnalyzer(
            TaskService(
                database,
                batch_checkpoint_writer=ComponentRunRepository.apply_batch_checkpoint,
            ),
            repository=StyleRepository(project_root=Path(project_root)),
            project_root=Path(project_root),
        ).run(token, assets)
        result_queue.put({"ok": True, "summary": summary.__dict__})
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
