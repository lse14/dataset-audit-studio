from __future__ import annotations

import logging
import os
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from dataset_audit_studio.adapters.component_run_repository import ComponentRunRepository
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import StaleWorkerToken, WorkerLeaseUnavailable
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.state_machine import WORKER_PHASES
from dataset_audit_studio.jobs.types import (
    ClaimedExportRun,
    ExportRunRunner,
    TaskView,
    WorkerToken,
)
from dataset_audit_studio.model_adapters.service import ModelService
from dataset_audit_studio.runtime import PROJECT_ROOT

LOGGER = logging.getLogger(__name__)
SUPPORTED_PHASES = WORKER_PHASES


class TaskRunner(Protocol):
    def run(self, token: WorkerToken, task: TaskView) -> object: ...


@dataclass(frozen=True)
class WorkerRuntimeContext:
    database: Database
    tasks: TaskService
    model_service: ModelService | None
    project_root: Path
    poll_seconds: float
    use_subprocess: bool


class WorkerRunnerFactory(Protocol):
    def __call__(self, context: WorkerRuntimeContext) -> TaskRunner: ...


class LocalWorker:
    def __init__(
        self,
        database: Database,
        *,
        model_service: ModelService | None = None,
        poll_seconds: float = 0.5,
        scoring_subprocess: bool = True,
        project_root: Path | None = None,
        runner_factory: WorkerRunnerFactory,
        export_run_runner: ExportRunRunner | None = None,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("Worker poll interval must be positive")
        self.database = database
        self.model_service = model_service
        self.poll_seconds = poll_seconds
        self.scoring_subprocess = scoring_subprocess
        self.project_root = (project_root or PROJECT_ROOT).resolve(strict=False)
        self.runner_factory = runner_factory
        self.export_run_runner = export_run_runner
        self.owner = f"local-{os.getpid()}-{uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._active_token: WorkerToken | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="dataset-audit-local-worker",
            daemon=True,
        )

    def start(self) -> None:
        if self._thread.is_alive():
            return
        if self._thread.ident is not None:
            raise RuntimeError("Local worker cannot be restarted")
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 60.0) -> bool:
        self._stop.set()
        self._request_active_pause()
        self._wake.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=max(0.0, timeout))
        return not self._thread.is_alive()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            token = self._active_token
        return {
            "enabled": True,
            "running": self._thread.is_alive() and not self._stop.is_set(),
            "owner": self.owner,
            "active_task_id": token.task_id if token is not None else None,
            "supported_phases": sorted(phase.value for phase in SUPPORTED_PHASES),
        }

    def _run(self) -> None:
        service = TaskService(
            self.database,
            batch_checkpoint_writer=ComponentRunRepository.apply_batch_checkpoint,
        )
        registry_runner = self.runner_factory(
            WorkerRuntimeContext(
                database=self.database,
                tasks=service,
                model_service=self.model_service,
                project_root=self.project_root,
                poll_seconds=self.poll_seconds,
                use_subprocess=self.scoring_subprocess,
            )
        )
        while not self._stop.is_set():
            try:
                claimed = service.claim_next(
                    owner=self.owner,
                    lease_seconds=300,
                    allowed_phases=SUPPORTED_PHASES,
                )
            except WorkerLeaseUnavailable:
                self._wait()
                continue
            except Exception:  # noqa: BLE001 - keep the local worker alive between tasks
                LOGGER.exception("Local worker could not claim a task")
                self._wait()
                continue

            if claimed is None:
                self._wait()
                continue

            if isinstance(claimed, ClaimedExportRun):
                if self.export_run_runner is None:
                    LOGGER.error(
                        "Local worker has no export run executor for %s",
                        claimed.export_run_id,
                    )
                    with suppress(StaleWorkerToken):
                        service.requeue_export_run(claimed.token)
                    self._wait()
                    continue
                try:
                    self.export_run_runner.run(claimed.token)
                except Exception:  # noqa: BLE001 - keep the local worker alive between runs
                    LOGGER.exception("Local worker export run %s failed", claimed.export_run_id)
                continue

            with self._lock:
                self._active_token = claimed.token
            try:
                if self._stop.is_set():
                    service.pause_claimed_before_work(claimed.token)
                    break
                registry_runner.run(claimed.token, claimed.task)
            except StaleWorkerToken:
                pass
            except Exception as error:  # noqa: BLE001 - persist task failure and continue
                LOGGER.exception("Local worker task %s failed", claimed.task.id)
                with suppress(StaleWorkerToken):
                    service.fail_worker(
                        claimed.token,
                        error_code=type(error).__name__,
                        error_message=str(error) or repr(error),
                    )
            finally:
                with self._lock:
                    self._active_token = None

    def _wait(self) -> None:
        self._wake.wait(self.poll_seconds)
        self._wake.clear()

    def _request_active_pause(self) -> None:
        with self._lock:
            token = self._active_token
        if token is None:
            return
        try:
            TaskService(self.database).request_pause(token.task_id)
        except Exception:  # noqa: BLE001 - shutdown must continue for terminal/racing tasks
            LOGGER.debug("Active worker task did not need a shutdown pause", exc_info=True)


def disabled_worker_snapshot() -> dict[str, Any]:
    return {
        "enabled": False,
        "running": False,
        "owner": None,
        "active_task_id": None,
        "supported_phases": sorted(phase.value for phase in SUPPORTED_PHASES),
    }
