from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from dataset_audit_studio.clustering.dedupe import (
    exact_duplicate_groups,
    visual_duplicate_groups,
)
from dataset_audit_studio.database.models import Evidence, Sample, Task, TaskConfig
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken

DUPLICATE_EVIDENCE_SOURCE = "duplicate_evidence.v1"
DUPLICATE_EVIDENCE_ALGORITHM = "duplicate_evidence.v1"
_DUPLICATE_CODES = ("duplicate_exact", "duplicate_visual")
_PHASH_MAX_DISTANCE = 8
_COLORHASH_MAX_DISTANCE = 8
_HEARTBEAT_SECONDS = 30.0
_WRITE_BATCH_SIZE = 1000


@dataclass(frozen=True)
class DuplicateEvidenceSummary:
    exact_groups: int
    visual_groups: int


class DuplicateEvidenceProducer:
    """Persist deterministic exact and visual duplicate evidence for one task."""

    def __init__(self, database: Database, *, tasks: TaskService | None = None) -> None:
        self.database = database
        self.tasks = tasks or TaskService(database)

    def produce(
        self, task_id: str, config_hash: str, *, token: WorkerToken | None = None
    ) -> DuplicateEvidenceSummary:
        if token is not None and token.task_id != task_id:
            raise ValueError("Duplicate evidence worker does not match the task")
        last_heartbeat = monotonic()

        def heartbeat(*, force: bool = False, session: Session | None = None) -> None:
            nonlocal last_heartbeat
            if token is not None and (force or monotonic() - last_heartbeat >= _HEARTBEAT_SECONDS):
                self.tasks.heartbeat(token, lease_seconds=300, session=session)
                last_heartbeat = monotonic()

        heartbeat(force=True)
        # Matching may take minutes on large datasets. Never hold SQLite's writer
        # lock while computing: heartbeats and task controls need that same lock.
        with self.database.read_session() as session:
            rows = session.execute(
                select(Sample.id, Sample.relative_path, Sample.source_sha256,
                       Sample.phash, Sample.colorhash)
                .where(Sample.task_id == task_id, Sample.scan_state == "valid")
                .order_by(Sample.relative_path, Sample.id)
            ).all()
        heartbeat(force=True)
        indices = tuple(range(len(rows)))

        def rank(index: int) -> tuple[str, str]:
            return rows[index].relative_path, rows[index].id

        exact = exact_duplicate_groups(
            indices, tuple(row.source_sha256 for row in rows), rank=rank,
        )
        heartbeat()
        visual = visual_duplicate_groups(
            indices,
            tuple(row.phash for row in rows),
            tuple(row.colorhash for row in rows),
            phash_max_distance=_PHASH_MAX_DISTANCE,
            colorhash_max_distance=_COLORHASH_MAX_DISTANCE,
            rank=rank,
            heartbeat=heartbeat,
        )
        heartbeat(force=True)

        # Replace the evidence atomically, fencing the worker under the write
        # lock so an expired/replaced execution cannot publish its results.
        with self.database.write_session() as session:
            heartbeat(force=True, session=session)
            task = session.get(Task, task_id)
            config = (
                session.scalar(
                    select(TaskConfig).where(
                        TaskConfig.task_id == task_id,
                        TaskConfig.revision == task.current_config_revision,
                    )
                )
                if task is not None
                else None
            )
            if config is None or config.config_hash != config_hash:
                raise RuntimeError("Duplicate evidence task configuration changed")
            session.execute(
                delete(Evidence).where(
                    Evidence.task_id == task_id,
                    Evidence.source == DUPLICATE_EVIDENCE_SOURCE,
                    Evidence.code.in_(_DUPLICATE_CODES),
                )
            )
            pending: list[dict] = []
            for code, groups in (("duplicate_exact", exact), ("duplicate_visual", visual)):
                for group in groups:
                    group_size = len(group.member_indices)
                    for index in group.member_indices:
                        pending.append(
                            dict(
                                task_id=task_id,
                                sample_id=rows[index].id,
                                code=code,
                                source=DUPLICATE_EVIDENCE_SOURCE,
                                value_json=group.group_key,
                                threshold_json=None,
                                value_number=float(group_size),
                                threshold_number=None,
                                metadata_json={
                                    "group_key": group.group_key,
                                    "group_size": group_size,
                                    "config_hash": config_hash,
                                    "provenance": {
                                        "component_id": "metrics.technical",
                                        "algorithm_version": DUPLICATE_EVIDENCE_ALGORITHM,
                                    },
                                },
                                severity="info",
                                review_only=True,
                                bbox_json=None,
                                algorithm_version=DUPLICATE_EVIDENCE_ALGORITHM,
                            )
                        )
                        if len(pending) >= _WRITE_BATCH_SIZE:
                            session.execute(insert(Evidence), pending)
                            pending.clear()
                            heartbeat(session=session)
            if pending:
                session.execute(insert(Evidence), pending)
            heartbeat(force=True, session=session)
            return DuplicateEvidenceSummary(len(exact), len(visual))
