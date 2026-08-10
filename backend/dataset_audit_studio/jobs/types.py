from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class TaskView:
    id: str
    name: str
    source_root: str
    output_root: str | None
    status: str
    resume_state: str | None
    current_config_revision: int
    config_hash: str
    config: dict[str, Any]
    progress_current: int
    progress_total: int | None
    row_version: int
    execution_epoch: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class TaskEventView:
    sequence: int
    event_type: str
    from_status: str | None
    to_status: str | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class CheckpointView:
    id: int
    task_id: str
    phase: str
    config_hash: str
    batch_index: int
    cursor: dict[str, Any]
    completed_items: int
    execution_epoch: int
    artifact_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class WorkerToken:
    task_id: str
    owner: str
    execution_epoch: int


@dataclass(frozen=True)
class ClaimedTask:
    token: WorkerToken
    task: TaskView


@dataclass(frozen=True)
class ExportRunToken:
    export_run_id: str
    owner: str
    execution_epoch: int


@dataclass(frozen=True)
class ClaimedExportRun:
    token: ExportRunToken
    export_run_id: str


class ExportRunRunner(Protocol):
    def run(self, token: ExportRunToken) -> object: ...


@dataclass(frozen=True)
class BatchCommitResult:
    task: TaskView
    checkpoint: CheckpointView
    control_state: Literal["continue", "paused", "terminated"]
    replayed: bool


class BatchCheckpointWriter(Protocol):
    def __call__(
        self,
        session: Session,
        *,
        task_id: str,
        config_hash: str,
        cursor: dict[str, Any],
        control_state: str,
        now: datetime,
    ) -> None: ...


@dataclass(frozen=True)
class WatermarkReclassificationResult:
    threshold: float
    updated: int
    candidates: int
