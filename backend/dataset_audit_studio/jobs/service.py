from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dataset_audit_studio.database.base import utc_now
from dataset_audit_studio.database.enums import ExportRunStatus, TaskStatus
from dataset_audit_studio.database.models import (
    Evidence,
    ExportRun,
    PhaseCheckpoint,
    Task,
    TaskConfig,
    TaskEvent,
    WorkerLease,
)
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import (
    CheckpointConflict,
    InvalidTaskTransition,
    StaleWorkerToken,
    TaskNotFound,
    TaskVersionConflict,
    WorkerLeaseUnavailable,
)
from dataset_audit_studio.jobs.profile import (
    has_builtin_profile,
    require_builtin_profile,
)
from dataset_audit_studio.jobs.state_machine import (
    TERMINAL_STATES,
    WORKER_PHASES,
    as_status,
    next_gate_status,
    next_pipeline_status,
    require_worker_phase,
)
from dataset_audit_studio.jobs.types import (
    BatchCheckpointWriter,
    BatchCommitResult,
    CheckpointView,
    ClaimedExportRun,
    ClaimedTask,
    ExportRunToken,
    TaskEventView,
    TaskView,
    WatermarkReclassificationResult,
    WorkerToken,
)


def canonical_config(config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    serialized = json.dumps(
        config,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    normalized = json.loads(serialized)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return normalized, digest


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TaskService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
        batch_checkpoint_writer: BatchCheckpointWriter | None = None,
    ) -> None:
        self.database = database
        self.clock = clock
        self.batch_checkpoint_writer = batch_checkpoint_writer

    def create_task(
        self,
        *,
        name: str,
        source_root: str,
        output_root: str | None,
        config: dict[str, Any],
    ) -> TaskView:
        require_builtin_profile(config)
        normalized, config_hash = canonical_config(config)
        with self.database.write_session() as session:
            task = Task(
                name=name,
                source_root=source_root,
                output_root=output_root,
                status=TaskStatus.DRAFT.value,
                current_config_revision=1,
            )
            session.add(task)
            session.flush()
            session.add(
                TaskConfig(
                    task_id=task.id,
                    revision=1,
                    config_hash=config_hash,
                    config_json=normalized,
                )
            )
            self._append_event(
                session,
                task,
                "task_created",
                None,
                TaskStatus.DRAFT,
                {"config_hash": config_hash, "config_revision": 1},
            )
            session.flush()
            return self._task_view(session, task)

    def get_task(self, task_id: str) -> TaskView:
        with self.database.read_session() as session:
            task = self._require_task(session, task_id)
            view = self._task_view(session, task)
            require_builtin_profile(view.config)
            return view

    def list_tasks(
        self, *, offset: int = 0, limit: int = 50, status: TaskStatus | None = None
    ) -> tuple[list[TaskView], int]:
        with self.database.read_session() as session:
            filters = [Task.status == status.value] if status is not None else []
            tasks = session.scalars(
                select(Task)
                .where(*filters)
                .order_by(Task.created_at.desc(), Task.id)
            ).all()
            visible = [
                task
                for task in tasks
                if has_builtin_profile(self._current_config(session, task).config_json)
            ]
            page = visible[offset : offset + limit]
            return [self._task_view(session, task) for task in page], len(visible)

    def update_config(
        self,
        task_id: str,
        config: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> TaskView:
        require_builtin_profile(config)
        normalized, config_hash = canonical_config(config)
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            current = self._current_config(session, task)
            require_builtin_profile(current.config_json)
            status = as_status(task.status)
            if status not in {TaskStatus.DRAFT, TaskStatus.PAUSED}:
                raise InvalidTaskTransition(
                    f"Config can only change while draft or paused, got {status.value}"
                )

            if current.config_hash == config_hash:
                return self._task_view(session, task)

            existing = session.scalar(
                select(TaskConfig).where(
                    TaskConfig.task_id == task.id, TaskConfig.config_hash == config_hash
                )
            )
            old_revision = task.current_config_revision
            if existing is None:
                revision = (
                    session.scalar(
                        select(func.max(TaskConfig.revision)).where(TaskConfig.task_id == task.id)
                    )
                    or 0
                ) + 1
                existing = TaskConfig(
                    task_id=task.id,
                    revision=revision,
                    config_hash=config_hash,
                    config_json=normalized,
                )
                session.add(existing)
                session.flush()

            task.current_config_revision = existing.revision
            task.progress_current = 0
            task.progress_total = None
            self._bump(task)
            self._append_event(
                session,
                task,
                "config_changed",
                status,
                status,
                {
                    "from_revision": old_revision,
                    "to_revision": existing.revision,
                    "config_hash": config_hash,
                },
            )
            session.flush()
            return self._task_view(session, task)

    def queue_task(self, task_id: str, *, expected_version: int | None = None) -> TaskView:
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            status = as_status(task.status)
            require_builtin_profile(self._current_config(session, task).config_json)
            if status is TaskStatus.QUEUED:
                return self._task_view(session, task)
            if status is not TaskStatus.DRAFT:
                raise InvalidTaskTransition(f"Only draft tasks can be queued, got {status.value}")
            task.status = TaskStatus.QUEUED.value
            task.resume_state = TaskStatus.SCANNING.value
            self._bump(task)
            self._append_event(
                session,
                task,
                "task_queued",
                status,
                TaskStatus.QUEUED,
                {"next_phase": task.resume_state},
            )
            session.flush()
            return self._task_view(session, task)

    def request_pause(self, task_id: str, *, expected_version: int | None = None) -> TaskView:
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            require_builtin_profile(self._current_config(session, task).config_json)
            status = as_status(task.status)
            if status in {TaskStatus.PAUSED, TaskStatus.PAUSING}:
                return self._task_view(session, task)

            lease = self._task_lease(session, task.id)
            if status is TaskStatus.QUEUED:
                target = TaskStatus.PAUSED
            elif status in WORKER_PHASES:
                task.resume_state = status.value
                target = TaskStatus.PAUSING if lease is not None else TaskStatus.PAUSED
            else:
                raise InvalidTaskTransition(f"Task cannot pause from {status.value}")

            task.status = target.value
            self._bump(task)
            self._append_event(
                session,
                task,
                "pause_requested" if target is TaskStatus.PAUSING else "task_paused",
                status,
                target,
                {"at_batch_boundary": target is TaskStatus.PAUSING},
            )
            session.flush()
            return self._task_view(session, task)

    def resume_task(self, task_id: str, *, expected_version: int | None = None) -> TaskView:
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            require_builtin_profile(self._current_config(session, task).config_json)
            status = as_status(task.status)
            if status is TaskStatus.QUEUED:
                return self._task_view(session, task)
            if status is not TaskStatus.PAUSED:
                raise InvalidTaskTransition(f"Only paused tasks can resume, got {status.value}")
            phase = require_worker_phase(task.resume_state or TaskStatus.SCANNING)
            task.status = TaskStatus.QUEUED.value
            task.resume_state = phase.value
            task.error_code = None
            task.error_message = None
            self._bump(task)
            self._append_event(
                session,
                task,
                "task_resumed",
                status,
                TaskStatus.QUEUED,
                {"next_phase": phase.value},
            )
            session.flush()
            return self._task_view(session, task)

    def request_terminate(
        self,
        task_id: str,
        *,
        force: bool = False,
        reason: str | None = None,
        expected_version: int | None = None,
    ) -> TaskView:
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            require_builtin_profile(self._current_config(session, task).config_json)
            status = as_status(task.status)
            if status in {TaskStatus.COMPLETED, TaskStatus.TERMINATED}:
                return self._task_view(session, task)

            lease = self._task_lease(session, task.id)
            immediate = force or lease is None or status not in WORKER_PHASES | {TaskStatus.PAUSING}
            if status is TaskStatus.TERMINATING and not force:
                return self._task_view(session, task)

            if immediate:
                self._release_lease(session, task.id)
                task.status = TaskStatus.TERMINATED.value
                task.resume_state = None
                task.finished_at = self.clock()
                task.execution_epoch += 1
                event_type = "task_force_terminated" if force else "task_terminated"
                target = TaskStatus.TERMINATED
            else:
                if status in WORKER_PHASES:
                    task.resume_state = status.value
                task.status = TaskStatus.TERMINATING.value
                event_type = "terminate_requested"
                target = TaskStatus.TERMINATING

            self._bump(task)
            self._append_event(
                session,
                task,
                event_type,
                status,
                target,
                {"force": force, "reason": reason},
            )
            session.flush()
            return self._task_view(session, task)

    def delete_task(
        self,
        task_id: str,
        *,
        expected_version: int | None = None,
    ) -> TaskView:
        """Delete a terminal task and its database-owned records.

        Task cache files are intentionally handled by the API layer after this
        transaction commits, so this service never receives arbitrary paths.
        """
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            require_builtin_profile(self._current_config(session, task).config_json)
            status = as_status(task.status)
            if status is TaskStatus.COMPLETED:
                raise InvalidTaskTransition("Completed tasks are immutable and cannot be deleted")
            if status not in TERMINAL_STATES:
                raise InvalidTaskTransition(
                    "Only completed, terminated, or failed tasks can be deleted, "
                    f"got {status.value}"
                )
            deleted = self._task_view(session, task)
            session.delete(task)
            session.flush()
            return deleted

    def claim_next(
        self,
        *,
        owner: str,
        lease_seconds: int = 60,
        allowed_phases: Collection[TaskStatus | str] | None = None,
    ) -> ClaimedTask | ClaimedExportRun | None:
        if not owner.strip():
            raise ValueError("Worker owner must not be empty")
        if lease_seconds < 5:
            raise ValueError("Worker lease must be at least 5 seconds")

        allowed_values = (
            frozenset(require_worker_phase(phase).value for phase in allowed_phases)
            if allowed_phases is not None
            else None
        )
        if allowed_values is not None and not allowed_values:
            return None

        with self.database.write_session() as session:
            now = self.clock()
            lease = session.get(WorkerLease, 1)
            if lease is not None:
                if _aware(lease.expires_at) > _aware(now):
                    target = (
                        f"task {lease.task_id}"
                        if lease.task_id is not None
                        else f"export run {lease.export_run_id}"
                    )
                    raise WorkerLeaseUnavailable(
                        f"Worker slot is held by {lease.owner} for {target}"
                    )
                self._recover_lease(session, lease, now)

            filters = [Task.status == TaskStatus.QUEUED.value]
            if allowed_values is not None:
                filters.append(Task.resume_state.in_(allowed_values))
            tasks = session.scalars(
                select(Task).where(*filters).order_by(Task.created_at, Task.id)
            ).all()
            task = next(
                (
                    candidate
                    for candidate in tasks
                    if has_builtin_profile(
                        self._current_config(session, candidate).config_json
                    )
                ),
                None,
            )
            if task is None:
                run = session.scalar(
                    select(ExportRun)
                    .where(ExportRun.status == ExportRunStatus.QUEUED.value)
                    .order_by(ExportRun.created_at, ExportRun.id)
                )
                if run is None:
                    return None
                run.status = ExportRunStatus.PLANNING.value
                run.execution_epoch += 1
                run.started_at = run.started_at or now
                run.error_code = None
                run.error_message = None
                lease = WorkerLease(
                    slot_id=1,
                    task_id=None,
                    export_run_id=run.id,
                    owner=owner,
                    execution_epoch=run.execution_epoch,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=now + timedelta(seconds=lease_seconds),
                )
                session.add(lease)
                session.flush()
                return ClaimedExportRun(
                    token=ExportRunToken(run.id, owner, run.execution_epoch),
                    export_run_id=run.id,
                )

            phase = require_worker_phase(task.resume_state or TaskStatus.SCANNING)
            previous = TaskStatus.QUEUED
            task.status = phase.value
            task.resume_state = None
            task.execution_epoch += 1
            task.started_at = task.started_at or now
            self._bump(task)
            lease = WorkerLease(
                slot_id=1,
                task_id=task.id,
                export_run_id=None,
                owner=owner,
                execution_epoch=task.execution_epoch,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=lease_seconds),
            )
            session.add(lease)
            self._append_event(
                session,
                task,
                "worker_claimed",
                previous,
                phase,
                {
                    "owner": owner,
                    "execution_epoch": task.execution_epoch,
                    "lease_seconds": lease_seconds,
                },
            )
            session.flush()
            token = WorkerToken(task.id, owner, task.execution_epoch)
            return ClaimedTask(token=token, task=self._task_view(session, task))

    def fail_worker(
        self,
        token: WorkerToken,
        *,
        error_code: str,
        error_message: str,
    ) -> TaskView:
        code = error_code.strip()[:80]
        message = error_message.strip()[:4000]
        if not code:
            raise ValueError("Worker failure code must not be empty")
        if not message:
            raise ValueError("Worker failure message must not be empty")

        with self.database.write_session() as session:
            now = self.clock()
            task, _ = self._require_worker(session, token, now)
            previous = as_status(task.status)
            task.status = TaskStatus.FAILED.value
            task.resume_state = None
            task.error_code = code
            task.error_message = message
            task.finished_at = now
            task.execution_epoch += 1
            self._release_lease(session, task.id)
            self._bump(task)
            self._append_event(
                session,
                task,
                "task_failed",
                previous,
                TaskStatus.FAILED,
                {"error_code": code, "worker_owner": token.owner},
            )
            session.flush()
            return self._task_view(session, task)

    def requeue_export_run(self, token: ExportRunToken) -> None:
        """Release a claimed run when this worker has no export executor composed."""
        with self.database.write_session() as session:
            run = session.get(ExportRun, token.export_run_id)
            lease = session.get(WorkerLease, 1)
            if (
                run is None
                or lease is None
                or lease.export_run_id != token.export_run_id
                or lease.owner != token.owner
                or lease.execution_epoch != token.execution_epoch
                or run.execution_epoch != token.execution_epoch
            ):
                raise StaleWorkerToken("Export run lease no longer matches the worker")
            run.status = ExportRunStatus.QUEUED.value
            run.execution_epoch += 1
            run.updated_at = self.clock()
            session.delete(lease)

    def pause_claimed_before_work(self, token: WorkerToken) -> TaskView:
        with self.database.write_session() as session:
            task, _ = self._require_worker(session, token, self.clock())
            previous = as_status(task.status)
            if previous in WORKER_PHASES:
                phase = previous
            elif previous is TaskStatus.PAUSING and task.resume_state is not None:
                phase = require_worker_phase(task.resume_state)
            else:
                raise InvalidTaskTransition(
                    f"Cannot pause an unstarted worker while task is {previous.value}"
                )
            task.status = TaskStatus.PAUSED.value
            task.resume_state = phase.value
            self._release_lease(session, task.id)
            self._bump(task)
            self._append_event(
                session,
                task,
                "task_paused",
                previous,
                TaskStatus.PAUSED,
                {"before_first_batch": True, "phase": phase.value},
            )
            session.flush()
            return self._task_view(session, task)

    def honor_claimed_control_before_work(
        self,
        token: WorkerToken,
        *,
        phase: TaskStatus | str,
    ) -> TaskView | None:
        worker_phase = require_worker_phase(phase)
        with self.database.write_session() as session:
            now = self.clock()
            task, _ = self._require_worker(session, token, now)
            previous = as_status(task.status)
            if previous is worker_phase:
                return None
            if (
                previous not in {TaskStatus.PAUSING, TaskStatus.TERMINATING}
                or task.resume_state != worker_phase.value
            ):
                raise InvalidTaskTransition(
                    f"Cannot start {worker_phase.value} while task is {previous.value}"
                )

            if previous is TaskStatus.PAUSING:
                task.status = TaskStatus.PAUSED.value
                task.resume_state = worker_phase.value
                target = TaskStatus.PAUSED
                event_type = "task_paused"
            else:
                task.status = TaskStatus.TERMINATED.value
                task.resume_state = None
                task.finished_at = now
                task.execution_epoch += 1
                target = TaskStatus.TERMINATED
                event_type = "task_terminated"
            self._release_lease(session, task.id)
            self._bump(task)
            self._append_event(
                session,
                task,
                event_type,
                previous,
                target,
                {"before_first_batch": True, "phase": worker_phase.value},
            )
            session.flush()
            return self._task_view(session, task)

    def heartbeat(self, token: WorkerToken, *, lease_seconds: int = 60) -> TaskView:
        if lease_seconds < 5:
            raise ValueError("Worker lease must be at least 5 seconds")
        with self.database.write_session() as session:
            now = self.clock()
            task, lease = self._require_worker(session, token, now)
            lease.heartbeat_at = now
            lease.expires_at = now + timedelta(seconds=lease_seconds)
            session.flush()
            return self._task_view(session, task)

    def recover_worker_after_process_stop(
        self,
        token: WorkerToken,
        *,
        reason: str,
    ) -> TaskView:
        """Finalize a control request after an isolated child is force-stopped.

        A blocked child cannot reach ``commit_batch`` to perform the normal
        batch-boundary transition. The parent still owns the lease, so it can
        safely retain the last checkpoint and release that lease here.
        """
        with self.database.write_session() as session:
            task, _ = self._require_worker(session, token, self.clock())
            previous = as_status(task.status)
            if previous not in {TaskStatus.PAUSING, TaskStatus.TERMINATING}:
                raise InvalidTaskTransition(
                    "Process-stop recovery requires a pending pause or termination"
                )
            phase = task.resume_state
            if previous is TaskStatus.PAUSING:
                if phase is None:
                    raise InvalidTaskTransition("Paused task is missing its resume phase")
                task.status = TaskStatus.PAUSED.value
                target = TaskStatus.PAUSED
                event_type = "task_paused"
                payload = {"after_process_stop": reason, "phase": phase}
            else:
                task.status = TaskStatus.TERMINATED.value
                task.resume_state = None
                task.finished_at = self.clock()
                task.execution_epoch += 1
                target = TaskStatus.TERMINATED
                event_type = "task_terminated"
                payload = {"after_process_stop": reason}
            self._release_lease(session, task.id)
            self._bump(task)
            self._append_event(session, task, event_type, previous, target, payload)
            session.flush()
            return self._task_view(session, task)

    def mark_phase_process_ready(
        self,
        token: WorkerToken,
        *,
        phase: TaskStatus | str,
        component_id: str,
    ) -> TaskView:
        """Record that an isolated phase runtime finished initialization."""
        worker_phase = require_worker_phase(phase)
        with self.database.write_session() as session:
            task, _ = self._require_worker(session, token, self.clock())
            status = as_status(task.status)
            if status is not worker_phase:
                raise InvalidTaskTransition(
                    f"Cannot mark {worker_phase.value} ready while task is {status.value}"
                )
            self._append_event(
                session,
                task,
                "phase_process_ready",
                status,
                status,
                {"phase": worker_phase.value, "component_id": component_id},
            )
            session.flush()
            return self._task_view(session, task)

    def commit_batch(
        self,
        token: WorkerToken,
        *,
        phase: TaskStatus | str,
        config_hash: str,
        batch_index: int,
        completed_items: int,
        cursor: dict[str, Any],
        progress_total: int | None = None,
        artifact_id: str | None = None,
        lease_seconds: int = 60,
        batch_writer: Callable[[Session], None] | None = None,
    ) -> BatchCommitResult:
        worker_phase = require_worker_phase(phase)
        if batch_index < 0 or completed_items < 0:
            raise ValueError("Batch index and completed items must be non-negative")
        if progress_total is not None and progress_total < completed_items:
            raise ValueError("Progress total cannot be less than completed items")

        with self.database.write_session() as session:
            existing = session.scalar(
                select(PhaseCheckpoint).where(
                    PhaseCheckpoint.task_id == token.task_id,
                    PhaseCheckpoint.phase == worker_phase.value,
                    PhaseCheckpoint.config_hash == config_hash,
                    PhaseCheckpoint.batch_index == batch_index,
                )
            )
            if existing is not None:
                return self._replay_checkpoint(
                    session,
                    token,
                    existing,
                    completed_items=completed_items,
                    cursor=cursor,
                    artifact_id=artifact_id,
                )

            now = self.clock()
            task, lease = self._require_worker(session, token, now)
            status = as_status(task.status)
            effective_phase = (
                require_worker_phase(task.resume_state)
                if status in {TaskStatus.PAUSING, TaskStatus.TERMINATING}
                else require_worker_phase(status)
            )
            if effective_phase is not worker_phase:
                raise CheckpointConflict(
                    f"Worker is in {effective_phase.value}, cannot commit {worker_phase.value}"
                )
            current_config = self._current_config(session, task)
            if current_config.config_hash != config_hash:
                raise CheckpointConflict("Checkpoint config hash is not the task's active config")
            if completed_items < task.progress_current:
                raise CheckpointConflict("Checkpoint would move progress backwards")

            if batch_writer is not None:
                batch_writer(session)

            checkpoint = PhaseCheckpoint(
                task_id=task.id,
                phase=worker_phase.value,
                config_hash=config_hash,
                batch_index=batch_index,
                cursor_json=canonical_config(cursor)[0],
                completed_items=completed_items,
                execution_epoch=token.execution_epoch,
                artifact_id=artifact_id,
                created_at=now,
            )
            session.add(checkpoint)
            task.progress_current = completed_items
            if progress_total is not None:
                task.progress_total = progress_total
            lease.heartbeat_at = now
            lease.expires_at = now + timedelta(seconds=max(lease_seconds, 5))
            self._bump(task)
            self._append_event(
                session,
                task,
                "batch_committed",
                status,
                status,
                {
                    "phase": worker_phase.value,
                    "batch_index": batch_index,
                    "completed_items": completed_items,
                    "config_hash": config_hash,
                },
            )

            control_state: str = "continue"
            if status is TaskStatus.PAUSING:
                task.status = TaskStatus.PAUSED.value
                self._release_lease(session, task.id)
                self._append_event(
                    session,
                    task,
                    "task_paused",
                    status,
                    TaskStatus.PAUSED,
                    {"after_batch": batch_index, "phase": worker_phase.value},
                )
                control_state = "paused"
            elif status is TaskStatus.TERMINATING:
                task.status = TaskStatus.TERMINATED.value
                task.resume_state = None
                task.finished_at = now
                task.execution_epoch += 1
                self._release_lease(session, task.id)
                self._append_event(
                    session,
                    task,
                    "task_terminated",
                    status,
                    TaskStatus.TERMINATED,
                    {"after_batch": batch_index, "phase": worker_phase.value},
                )
                control_state = "terminated"

            if self.batch_checkpoint_writer is not None:
                self.batch_checkpoint_writer(
                    session,
                    task_id=task.id,
                    config_hash=config_hash,
                    cursor=cursor,
                    control_state=control_state,
                    now=now,
                )

            session.flush()
            return BatchCommitResult(
                task=self._task_view(session, task),
                checkpoint=self._checkpoint_view(checkpoint),
                control_state=control_state,  # type: ignore[arg-type]
                replayed=False,
            )

    def complete_phase(self, token: WorkerToken, *, phase: TaskStatus | str) -> TaskView:
        worker_phase = require_worker_phase(phase)
        with self.database.write_session() as session:
            task, _ = self._require_worker(session, token, self.clock())
            status = as_status(task.status)
            if status is not worker_phase:
                raise InvalidTaskTransition(
                    f"Cannot complete {worker_phase.value} while task is {status.value}"
                )
            target = self._completion_target(session, task, worker_phase)
            self._release_lease(session, task.id)
            task.progress_current = 0
            task.progress_total = None
            if target in WORKER_PHASES:
                task.status = TaskStatus.QUEUED.value
                task.resume_state = target.value
                visible_target = TaskStatus.QUEUED
            else:
                task.status = target.value
                task.resume_state = None
                visible_target = target
                if target is TaskStatus.COMPLETED:
                    task.finished_at = self.clock()
            self._bump(task)
            self._append_event(
                session,
                task,
                "phase_completed",
                status,
                visible_target,
                {"phase": worker_phase.value, "next_phase": target.value},
            )
            session.flush()
            return self._task_view(session, task)

    def reclassify_watermark_evidence(
        self,
        task_id: str,
        *,
        threshold: float,
        expected_version: int | None = None,
    ) -> WatermarkReclassificationResult:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("watermark threshold must be between 0 and 1")
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            require_builtin_profile(self._current_config(session, task).config_json)
            status = as_status(task.status)
            can_reclassify = status is TaskStatus.EVIDENCE_REVIEW or (
                status is TaskStatus.PAUSED
                and task.resume_state
                in {
                    TaskStatus.EVIDENCE_REVIEW.value,
                }
            )
            if not can_reclassify:
                raise InvalidTaskTransition(
                    "Watermark review threshold can only change while awaiting review"
                )
            evidence_rows = session.scalars(
                select(Evidence).where(
                    Evidence.task_id == task_id,
                    Evidence.code == "watermark_probability",
                )
            ).all()
            candidates = 0
            for evidence in evidence_rows:
                candidate = (
                    evidence.value_number is not None
                    and float(evidence.value_number) >= threshold
                )
                evidence.threshold_json = threshold
                evidence.threshold_number = threshold
                evidence.severity = "medium" if candidate else "info"
                metadata = dict(evidence.metadata_json)
                metadata["candidate"] = candidate
                evidence.metadata_json = metadata
                candidates += int(candidate)
            self._bump(task)
            self._append_event(
                session,
                task,
                "watermark_review_threshold_changed",
                status,
                status,
                {
                    "threshold": threshold,
                    "updated": len(evidence_rows),
                    "candidates": candidates,
                },
            )
            session.flush()
            return WatermarkReclassificationResult(
                threshold=threshold,
                updated=len(evidence_rows),
                candidates=candidates,
            )

    def release_review_gate(
        self,
        task_id: str,
        *,
        expected_gate: TaskStatus | str,
        expected_version: int | None = None,
    ) -> TaskView:
        gate = as_status(expected_gate)
        next_phase = next_gate_status(gate)
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            config = self._current_config(session, task).config_json
            require_builtin_profile(config)
            if self._is_copy_export_config(config):
                raise InvalidTaskTransition("copy export must use ExportRunService")
            status = as_status(task.status)
            if status is not gate:
                raise InvalidTaskTransition(
                    f"Expected review gate {gate.value}, got {status.value}"
                )
            task.status = TaskStatus.QUEUED.value
            task.resume_state = next_phase.value
            self._bump(task)
            self._append_event(
                session,
                task,
                "review_gate_released",
                status,
                TaskStatus.QUEUED,
                {"next_phase": next_phase.value},
            )
            session.flush()
            return self._task_view(session, task)

    def confirm_rewrite_preview(
        self,
        task_id: str,
        *,
        preview_digest: str,
        expected_version: int | None = None,
    ) -> TaskView:
        if len(preview_digest) != 64 or any(
            character not in "0123456789abcdef" for character in preview_digest
        ):
            raise ValueError("Rewrite preview digest must be a lowercase SHA-256 value")
        with self.database.write_session() as session:
            task = self._require_task(session, task_id)
            self._require_version(task, expected_version)
            require_builtin_profile(self._current_config(session, task).config_json)
            status = as_status(task.status)
            allowed = status is TaskStatus.EVIDENCE_REVIEW or (
                status is TaskStatus.PAUSED
                and task.resume_state == TaskStatus.EVIDENCE_REVIEW.value
            )
            if not allowed:
                raise InvalidTaskTransition(
                    "Rewrite preview can only be confirmed during evidence review"
                )
            self._bump(task)
            self._append_event(
                session,
                task,
                "rewrite_preview_confirmed",
                status,
                status,
                {
                    "preview_digest": preview_digest,
                    "config_revision": task.current_config_revision,
                    "config_hash": self._current_config(session, task).config_hash,
                },
            )
            session.flush()
            return self._task_view(session, task)

    @staticmethod
    def _completion_target(
        session: Session,
        task: Task,
        worker_phase: TaskStatus,
    ) -> TaskStatus:
        return next_pipeline_status(worker_phase)

    def recover_stale_leases(self) -> list[TaskView]:
        with self.database.write_session() as session:
            now = self.clock()
            lease = session.get(WorkerLease, 1)
            if lease is None or _aware(lease.expires_at) > _aware(now):
                return []
            recovered = self._recover_lease(session, lease, now)
            session.flush()
            return [self._task_view(session, recovered)] if isinstance(recovered, Task) else []

    def list_events(
        self,
        task_id: str,
        *,
        after: int = 0,
        limit: int = 200,
    ) -> list[TaskEventView]:
        with self.database.read_session() as session:
            task = self._require_task(session, task_id)
            require_builtin_profile(self._current_config(session, task).config_json)
            events = session.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id, TaskEvent.sequence > after)
                .order_by(TaskEvent.sequence)
                .limit(limit)
            ).all()
            return [self._event_view(event) for event in events]

    def latest_event_sequence(self, task_id: str) -> int:
        with self.database.read_session() as session:
            task = self._require_task(session, task_id)
            require_builtin_profile(self._current_config(session, task).config_json)
            return (
                session.scalar(
                    select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task_id)
                )
                or 0
            )

    def list_checkpoints(self, task_id: str, *, phase: str | None = None) -> list[CheckpointView]:
        with self.database.read_session() as session:
            task = self._require_task(session, task_id)
            require_builtin_profile(self._current_config(session, task).config_json)
            filters = [PhaseCheckpoint.task_id == task_id]
            if phase is not None:
                filters.append(PhaseCheckpoint.phase == require_worker_phase(phase).value)
            checkpoints = session.scalars(
                select(PhaseCheckpoint)
                .where(*filters)
                .order_by(PhaseCheckpoint.phase, PhaseCheckpoint.batch_index)
            ).all()
            return [self._checkpoint_view(item) for item in checkpoints]

    def _recover_lease(
        self, session: Session, lease: WorkerLease, now: datetime
    ) -> Task | ExportRun | None:
        if lease.export_run_id is not None:
            run = session.get(ExportRun, lease.export_run_id)
            if run is None:
                session.delete(lease)
                return None
            if run.status in {
                ExportRunStatus.PLANNING.value,
                ExportRunStatus.COPYING.value,
                ExportRunStatus.VERIFYING.value,
                ExportRunStatus.PUBLISHING.value,
            }:
                run.status = ExportRunStatus.QUEUED.value
                run.execution_epoch += 1
            session.delete(lease)
            return run
        task = session.get(Task, lease.task_id)
        if task is None:
            session.delete(lease)
            return None
        if not has_builtin_profile(self._current_config(session, task).config_json):
            previous = as_status(task.status)
            task.status = TaskStatus.FAILED.value
            task.resume_state = None
            task.finished_at = now
            task.execution_epoch += 1
            task.error_code = "legacy_task_config_unsupported"
            task.error_message = (
                "Profile-free task configuration is no longer supported; "
                "the expired worker lease was not restored"
            )
            self._bump(task)
            session.delete(lease)
            self._append_event(
                session,
                task,
                "legacy_task_rejected",
                previous,
                TaskStatus.FAILED,
                {"error_code": task.error_code},
            )
            return None
        status = as_status(task.status)
        if status is TaskStatus.TERMINATING:
            task.status = TaskStatus.TERMINATED.value
            task.resume_state = None
            task.finished_at = now
            target = TaskStatus.TERMINATED
        else:
            if status in WORKER_PHASES:
                task.resume_state = status.value
            elif status is TaskStatus.PAUSING and task.resume_state is None:
                task.resume_state = TaskStatus.SCANNING.value
            task.status = TaskStatus.PAUSED.value
            target = TaskStatus.PAUSED
        task.execution_epoch += 1
        task.error_code = "worker_lease_expired"
        task.error_message = (
            f"Worker {lease.owner} stopped heartbeating; last committed batch retained"
        )
        self._bump(task)
        session.delete(lease)
        self._append_event(
            session,
            task,
            "stale_worker_recovered",
            status,
            target,
            {"owner": lease.owner, "expired_at": lease.expires_at.isoformat()},
        )
        return task

    def _replay_checkpoint(
        self,
        session: Session,
        token: WorkerToken,
        checkpoint: PhaseCheckpoint,
        *,
        completed_items: int,
        cursor: dict[str, Any],
        artifact_id: str | None,
    ) -> BatchCommitResult:
        normalized_cursor = canonical_config(cursor)[0]
        if (
            checkpoint.execution_epoch != token.execution_epoch
            or checkpoint.completed_items != completed_items
            or checkpoint.cursor_json != normalized_cursor
            or checkpoint.artifact_id != artifact_id
        ):
            raise CheckpointConflict("Batch identity already exists with different committed data")
        task = self._require_task(session, token.task_id)
        status = as_status(task.status)
        if status is TaskStatus.PAUSED:
            control_state = "paused"
        elif status is TaskStatus.TERMINATED:
            control_state = "terminated"
        elif status in WORKER_PHASES:
            self._require_worker(session, token, self.clock())
            if status.value != checkpoint.phase:
                raise StaleWorkerToken(
                    f"Task advanced to {status.value}; checkpoint belongs to {checkpoint.phase}"
                )
            control_state = "continue"
        elif status in {TaskStatus.PAUSING, TaskStatus.TERMINATING}:
            self._require_worker(session, token, self.clock())
            if task.resume_state != checkpoint.phase:
                raise StaleWorkerToken(
                    f"Control request targets {task.resume_state}; checkpoint belongs to "
                    f"{checkpoint.phase}"
                )
            previous = status
            self._release_lease(session, task.id)
            if status is TaskStatus.PAUSING:
                task.status = TaskStatus.PAUSED.value
                target = TaskStatus.PAUSED
                event_type = "task_paused"
                control_state = "paused"
            else:
                task.status = TaskStatus.TERMINATED.value
                task.resume_state = None
                task.finished_at = self.clock()
                task.execution_epoch += 1
                target = TaskStatus.TERMINATED
                event_type = "task_terminated"
                control_state = "terminated"
            self._bump(task)
            self._append_event(
                session,
                task,
                event_type,
                previous,
                target,
                {"after_replayed_batch": checkpoint.batch_index, "phase": checkpoint.phase},
            )
            session.flush()
        else:
            raise StaleWorkerToken(
                f"Task is now {status.value}; checkpoint replay cannot continue old worker"
            )
        return BatchCommitResult(
            task=self._task_view(session, task),
            checkpoint=self._checkpoint_view(checkpoint),
            control_state=control_state,  # type: ignore[arg-type]
            replayed=True,
        )

    def _require_worker(
        self, session: Session, token: WorkerToken, now: datetime
    ) -> tuple[Task, WorkerLease]:
        task = self._require_task(session, token.task_id)
        lease = session.get(WorkerLease, 1)
        if (
            lease is None
            or lease.task_id != token.task_id
            or lease.owner != token.owner
            or lease.execution_epoch != token.execution_epoch
            or task.execution_epoch != token.execution_epoch
        ):
            raise StaleWorkerToken("Worker lease or execution epoch no longer matches")
        if _aware(lease.expires_at) <= _aware(now):
            raise StaleWorkerToken("Worker lease has expired")
        return task, lease

    @staticmethod
    def _release_lease(session: Session, task_id: str) -> None:
        lease = session.get(WorkerLease, 1)
        if lease is not None and lease.task_id == task_id:
            session.delete(lease)

    @staticmethod
    def _task_lease(session: Session, task_id: str) -> WorkerLease | None:
        lease = session.get(WorkerLease, 1)
        return lease if lease is not None and lease.task_id == task_id else None

    @staticmethod
    def _require_task(session: Session, task_id: str) -> Task:
        task = session.get(Task, task_id)
        if task is None:
            raise TaskNotFound(f"Task not found: {task_id}")
        return task

    @staticmethod
    def _require_version(task: Task, expected_version: int | None) -> None:
        if expected_version is not None and task.row_version != expected_version:
            raise TaskVersionConflict(
                f"Task version is {task.row_version}, expected {expected_version}"
            )

    @staticmethod
    def _current_config(session: Session, task: Task) -> TaskConfig:
        config = session.scalar(
            select(TaskConfig).where(
                TaskConfig.task_id == task.id,
                TaskConfig.revision == task.current_config_revision,
            )
        )
        if config is None:
            raise RuntimeError(f"Task {task.id} has no active config revision")
        return config

    @staticmethod
    def _is_copy_export_config(config: object) -> bool:
        if not isinstance(config, dict):
            return False
        components = config.get("components")
        if not isinstance(components, dict):
            return False
        export = components.get("export.dataset")
        if not isinstance(export, dict):
            return False
        settings = export.get("config")
        return isinstance(settings, dict) and settings.get("mode") == "copy"

    @staticmethod
    def _bump(task: Task) -> None:
        task.row_version += 1
        task.updated_at = utc_now()

    @staticmethod
    def _append_event(
        session: Session,
        task: Task,
        event_type: str,
        from_status: TaskStatus | None,
        to_status: TaskStatus | None,
        payload: dict[str, Any],
    ) -> None:
        sequence = (
            session.scalar(select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task.id))
            or 0
        ) + 1
        session.add(
            TaskEvent(
                task_id=task.id,
                sequence=sequence,
                event_type=event_type,
                from_status=from_status.value if from_status is not None else None,
                to_status=to_status.value if to_status is not None else None,
                payload_json=payload,
            )
        )

    def _task_view(self, session: Session, task: Task) -> TaskView:
        config = self._current_config(session, task)
        lease = self._task_lease(session, task.id)
        return TaskView(
            id=task.id,
            name=task.name,
            source_root=task.source_root,
            output_root=task.output_root,
            status=task.status,
            resume_state=task.resume_state,
            current_config_revision=task.current_config_revision,
            config_hash=config.config_hash,
            config=dict(config.config_json),
            progress_current=int(task.progress_current),
            progress_total=int(task.progress_total) if task.progress_total is not None else None,
            row_version=task.row_version,
            execution_epoch=task.execution_epoch,
            lease_owner=lease.owner if lease is not None else None,
            lease_expires_at=lease.expires_at if lease is not None else None,
            error_code=task.error_code,
            error_message=task.error_message,
            created_at=task.created_at,
            updated_at=task.updated_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
        )

    @staticmethod
    def _event_view(event: TaskEvent) -> TaskEventView:
        return TaskEventView(
            sequence=event.sequence,
            event_type=event.event_type,
            from_status=event.from_status,
            to_status=event.to_status,
            payload=dict(event.payload_json),
            created_at=event.created_at,
        )

    @staticmethod
    def _checkpoint_view(checkpoint: PhaseCheckpoint) -> CheckpointView:
        return CheckpointView(
            id=checkpoint.id,
            task_id=checkpoint.task_id,
            phase=checkpoint.phase,
            config_hash=checkpoint.config_hash,
            batch_index=checkpoint.batch_index,
            cursor=dict(checkpoint.cursor_json),
            completed_items=int(checkpoint.completed_items),
            execution_epoch=checkpoint.execution_epoch,
            artifact_id=checkpoint.artifact_id,
            created_at=checkpoint.created_at,
        )
