from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from dataset_audit_studio.clustering.repository import ClusteringRepository
from dataset_audit_studio.clustering.service import SemanticClusterer
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.phase_process import run_isolated_phase_subprocess
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT, assert_runtime_isolated
from dataset_audit_studio.scoring.types import RuntimeAssets


def run_clustering_subprocess(
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
        phase_name="clustering",
        entrypoint=_clustering_process_entry,
        project_root=project_root,
        poll_seconds=poll_seconds,
    )


def _clustering_process_entry(
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
        project = Path(project_root)
        summary = SemanticClusterer(
            TaskService(database),
            repository=ClusteringRepository(project_root=project),
            project_root=project,
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
