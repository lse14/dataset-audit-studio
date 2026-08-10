from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select

from dataset_audit_studio.clustering.dedupe import (
    exact_duplicate_groups,
    visual_duplicate_groups,
)
from dataset_audit_studio.database.models import Evidence, Sample, Task, TaskConfig
from dataset_audit_studio.database.session import Database

DUPLICATE_EVIDENCE_SOURCE = "duplicate_evidence.v1"
DUPLICATE_EVIDENCE_ALGORITHM = "duplicate_evidence.v1"
_DUPLICATE_CODES = ("duplicate_exact", "duplicate_visual")
_PHASH_MAX_DISTANCE = 8
_COLORHASH_MAX_DISTANCE = 8


@dataclass(frozen=True)
class DuplicateEvidenceSummary:
    exact_groups: int
    visual_groups: int


class DuplicateEvidenceProducer:
    """Persist deterministic exact and visual duplicate evidence for one task."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def produce(self, task_id: str, config_hash: str) -> DuplicateEvidenceSummary:
        with self.database.write_session() as session:
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
            rows = session.scalars(
                select(Sample)
                .where(Sample.task_id == task_id, Sample.scan_state == "valid")
                .order_by(Sample.relative_path, Sample.id)
            ).all()
            session.execute(
                delete(Evidence).where(
                    Evidence.task_id == task_id,
                    Evidence.source == DUPLICATE_EVIDENCE_SOURCE,
                    Evidence.code.in_(_DUPLICATE_CODES),
                )
            )
            indices = tuple(range(len(rows)))

            def rank(index: int) -> tuple[str, str]:
                return rows[index].relative_path, rows[index].id

            exact = exact_duplicate_groups(
                indices,
                tuple(row.source_sha256 for row in rows),
                rank=rank,
            )
            visual = visual_duplicate_groups(
                indices,
                tuple(row.phash for row in rows),
                tuple(row.colorhash for row in rows),
                phash_max_distance=_PHASH_MAX_DISTANCE,
                colorhash_max_distance=_COLORHASH_MAX_DISTANCE,
                rank=rank,
            )
            for code, groups in (("duplicate_exact", exact), ("duplicate_visual", visual)):
                for group in groups:
                    members = tuple(rows[index] for index in group.member_indices)
                    for member in members:
                        session.add(
                            Evidence(
                                task_id=task_id,
                                sample_id=member.id,
                                code=code,
                                source=DUPLICATE_EVIDENCE_SOURCE,
                                value_json=group.group_key,
                                threshold_json=None,
                                value_number=float(len(members)),
                                threshold_number=None,
                                metadata_json={
                                    "group_key": group.group_key,
                                    "group_size": len(members),
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
            return DuplicateEvidenceSummary(len(exact), len(visual))
