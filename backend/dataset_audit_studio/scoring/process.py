from __future__ import annotations

import multiprocessing
import queue
import time
import traceback
from pathlib import Path
from typing import Any

from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT, assert_runtime_isolated
from dataset_audit_studio.scoring.errors import ScoringProcessError
from dataset_audit_studio.scoring.repository import ScoringRepository
from dataset_audit_studio.scoring.service import ModelScorer
from dataset_audit_studio.scoring.types import RuntimeAssets


def run_scoring_subprocess(
    database: Database,
    tasks: TaskService,
    token: WorkerToken,
    assets: RuntimeAssets,
    *,
    project_root: Path = PROJECT_ROOT,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_scoring_process_entry,
        args=(
            str(database.path),
            token,
            assets,
            str(project_root),
            result_queue,
        ),
        name=f"dataset-scoring-{token.task_id[:8]}",
    )
    process.start()
    last_heartbeat = time.monotonic()
    forced_stop = False
    while process.is_alive():
        process.join(timeout=poll_seconds)
        current = tasks.get_task(token.task_id)
        if current.status in {TaskStatus.TERMINATED.value, TaskStatus.FAILED.value}:
            process.terminate()
            forced_stop = True
            break
        if time.monotonic() - last_heartbeat >= 30.0:
            try:
                tasks.heartbeat(token, lease_seconds=300)
            except StaleWorkerToken:
                process.terminate()
                forced_stop = True
                break
            last_heartbeat = time.monotonic()
    process.join(timeout=30)
    if process.is_alive():
        process.kill()
        process.join(timeout=10)
    if forced_stop:
        return {"status": tasks.get_task(token.task_id).status, "forced_stop": True}
    try:
        message = result_queue.get(timeout=2)
    except queue.Empty as error:
        raise ScoringProcessError(
            f"Scoring process exited with code {process.exitcode} without a result"
        ) from error
    finally:
        result_queue.close()
        result_queue.join_thread()
    if not message.get("ok"):
        raise ScoringProcessError(
            f"{message.get('error_type', 'ScoringError')}: {message.get('error', '')}\n"
            f"{message.get('traceback', '')}"
        )
    return dict(message["summary"])


def _scoring_process_entry(
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
        summary = ModelScorer(
            TaskService(database),
            repository=ScoringRepository(project_root=Path(project_root)),
        ).run(token, assets)
        result_queue.put({"ok": True, "summary": summary.__dict__})
    except Exception as error:  # noqa: BLE001 - serialize the child failure to the owner
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
