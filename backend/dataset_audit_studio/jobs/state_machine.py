from __future__ import annotations

from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.jobs.errors import InvalidTaskTransition

WORKER_PHASES = frozenset(
    {
        TaskStatus.SCANNING,
        TaskStatus.CPU_METRICS,
        TaskStatus.MODEL_SCORING,
        TaskStatus.STYLE_ANALYSIS,
        TaskStatus.SEMANTIC_CLUSTERING,
        TaskStatus.EXPORTING,
    }
)

REVIEW_GATES = frozenset({TaskStatus.EVIDENCE_REVIEW})
TERMINAL_STATES = frozenset({TaskStatus.COMPLETED, TaskStatus.TERMINATED, TaskStatus.FAILED})

PIPELINE_NEXT: dict[TaskStatus, TaskStatus] = {
    TaskStatus.SCANNING: TaskStatus.CPU_METRICS,
    TaskStatus.CPU_METRICS: TaskStatus.MODEL_SCORING,
    TaskStatus.MODEL_SCORING: TaskStatus.STYLE_ANALYSIS,
    TaskStatus.STYLE_ANALYSIS: TaskStatus.SEMANTIC_CLUSTERING,
    TaskStatus.SEMANTIC_CLUSTERING: TaskStatus.EVIDENCE_REVIEW,
    TaskStatus.EXPORTING: TaskStatus.COMPLETED,
}

GATE_NEXT: dict[TaskStatus, TaskStatus] = {TaskStatus.EVIDENCE_REVIEW: TaskStatus.EXPORTING}


def as_status(value: str | TaskStatus) -> TaskStatus:
    try:
        return value if isinstance(value, TaskStatus) else TaskStatus(value)
    except ValueError as error:
        raise InvalidTaskTransition(f"Unknown task status: {value}") from error


def require_worker_phase(value: str | TaskStatus) -> TaskStatus:
    status = as_status(value)
    if status not in WORKER_PHASES:
        raise InvalidTaskTransition(f"Status is not a worker phase: {status.value}")
    return status


def next_pipeline_status(phase: str | TaskStatus) -> TaskStatus:
    status = require_worker_phase(phase)
    try:
        return PIPELINE_NEXT[status]
    except KeyError as error:
        raise InvalidTaskTransition(f"Phase has no completion target: {status.value}") from error


def next_gate_status(gate: str | TaskStatus) -> TaskStatus:
    status = as_status(gate)
    try:
        return GATE_NEXT[status]
    except KeyError as error:
        raise InvalidTaskTransition(f"Status is not a review gate: {status.value}") from error
