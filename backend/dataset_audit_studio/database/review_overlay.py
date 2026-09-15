from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataset_audit_studio.database.models import ReviewDecision

HUMAN_OVERLAY_DECISIONS = ("approved_exclude", "approved_keep")


def active_human_overlay_by_sample(
    session: Session,
    *,
    task_id: str,
    sample_ids: Iterable[str],
) -> dict[str, tuple[ReviewDecision, ...]]:
    ids = tuple(dict.fromkeys(sample_ids))
    if not ids:
        return {}
    grouped: dict[str, list[ReviewDecision]] = {}
    # Bound SQLite parameters independently of the dataset size.
    for offset in range(0, len(ids), 500):
        rows = session.scalars(
            select(ReviewDecision)
            .where(
                ReviewDecision.task_id == task_id,
                ReviewDecision.sample_id.in_(ids[offset : offset + 500]),
                ReviewDecision.source == "human",
                ReviewDecision.decision.in_(HUMAN_OVERLAY_DECISIONS),
                ReviewDecision.is_active.is_(True),
            )
            .order_by(ReviewDecision.created_at.desc(), ReviewDecision.id.desc())
        ).all()
        for row in rows:
            if row.sample_id is not None:
                grouped.setdefault(row.sample_id, []).append(row)
    return {sample_id: tuple(items) for sample_id, items in grouped.items()}
