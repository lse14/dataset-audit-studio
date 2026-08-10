from __future__ import annotations

import multiprocessing
import queue
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT

PhaseEntrypoint = Callable[[str, WorkerToken, Any, str, Any], None]


class PhaseProcessError(RuntimeError):
    pass


def run_isolated_phase_subprocess(
    database: Database,
    tasks: TaskService,
    token: WorkerToken,
    payload: Any,
    *,
    phase_name: str,
    entrypoint: PhaseEntrypoint,
    project_root: Path = PROJECT_ROOT,
    poll_seconds: float = 0.5,
    startup_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    if startup_timeout_seconds is not None and startup_timeout_seconds <= 0:
        raise ValueError("Startup timeout must be positive when configured")
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=entrypoint,
        args=(
            str(database.path),
            token,
            payload,
            str(project_root),
            result_queue,
        ),
        name=f"dataset-{phase_name}-{token.task_id[:8]}",
    )
    process.start()
    try:
        last_heartbeat = time.monotonic()
        started_at = last_heartbeat
        initial_progress = tasks.get_task(token.task_id).progress_current
        initial_event_sequence = tasks.latest_event_sequence(token.task_id)
        forced_stop = False
        control_requested: str | None = None
        timed_out = False
        process_ready = False
        message: dict[str, Any] | None = None
        while process.is_alive():
            process.join(timeout=poll_seconds)
            current = tasks.get_task(token.task_id)
            if current.status in {TaskStatus.TERMINATED.value, TaskStatus.FAILED.value}:
                process.terminate()
                forced_stop = True
                break
            if current.status in {
                TaskStatus.PAUSING.value,
                TaskStatus.TERMINATING.value,
            }:
                process.terminate()
                forced_stop = True
                control_requested = current.status
                break
            if not process_ready:
                process_ready = (
                    tasks.latest_event_sequence(token.task_id) > initial_event_sequence
                )
            if (
                startup_timeout_seconds is not None
                and not process_ready
                and current.progress_current <= initial_progress
                and time.monotonic() - started_at >= startup_timeout_seconds
            ):
                process.terminate()
                forced_stop = True
                timed_out = True
                break
            if time.monotonic() - last_heartbeat >= 30.0:
                try:
                    tasks.heartbeat(token, lease_seconds=300)
                except StaleWorkerToken:
                    process.terminate()
                    forced_stop = True
                    break
                last_heartbeat = time.monotonic()
            with suppress(queue.Empty):
                message = result_queue.get(timeout=poll_seconds)
            if message is not None:
                break
        process.join(timeout=30)
        if process.is_alive():
            process.kill()
            process.join(timeout=10)
        if forced_stop:
            if timed_out:
                try:
                    failed = tasks.fail_worker(
                        token,
                            error_code="phase_process_startup_timeout",
                            error_message=(
                                f"{phase_name} did not report runtime readiness within "
                                f"{startup_timeout_seconds:.0f} seconds"
                            ),
                    )
                    return {
                        "status": failed.status,
                        "forced_stop": True,
                        "timeout": True,
                    }
                except StaleWorkerToken:
                    return {"status": tasks.get_task(token.task_id).status, "forced_stop": True}
            if control_requested is not None:
                try:
                    recovered = tasks.recover_worker_after_process_stop(
                        token,
                        reason=f"{phase_name} child process stopped for {control_requested}",
                    )
                    return {"status": recovered.status, "forced_stop": True}
                except StaleWorkerToken:
                    return {"status": tasks.get_task(token.task_id).status, "forced_stop": True}
            return {"status": tasks.get_task(token.task_id).status, "forced_stop": True}
        if message is None:
            try:
                message = result_queue.get(timeout=2)
            except queue.Empty as error:
                raise PhaseProcessError(
                    f"{phase_name} process exited with code {process.exitcode} without a result"
                ) from error
        if not message.get("ok"):
            raise PhaseProcessError(
                f"{message.get('error_type', 'PhaseError')}: {message.get('error', '')}\n"
                f"{message.get('traceback', '')}"
            )
        return dict(message["summary"])
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join(timeout=10)
        result_queue.close()
        result_queue.join_thread()
