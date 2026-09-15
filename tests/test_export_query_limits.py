from __future__ import annotations

import sqlite3

from dataset_audit_studio.database.models import Evidence, ReviewDecision, Sample
from dataset_audit_studio.database.review_overlay import active_human_overlay_by_sample
from dataset_audit_studio.export_runs.eligibility import EligibilityResolver
from dataset_audit_studio.export_runs.planner import ExportRunPlanner
from dataset_audit_studio.export_runs.service import ExportRunService
from sqlalchemy import event, select
from test_r10_3_2_first_export import _review_task


def test_large_preview_and_creation_preserve_manual_exclusions(database, task_service, tmp_path):
    task = _review_task(database, task_service, tmp_path)
    with database.write_session() as session:
        original = session.scalar(select(Sample).where(Sample.task_id == task.id))
        values = {
            column.name: getattr(original, column.name) for column in Sample.__table__.columns
        }
        for index in range(1001):
            path = tmp_path / "source" / f"sample-{index}.png"
            path.write_bytes(b"r1032-first-export")
            session.add(
                Sample(
                    **{
                        **values,
                        "id": f"sample-{index}",
                        "relative_path": path.name,
                        "source_mtime_ns": path.stat().st_mtime_ns,
                    }
                )
            )
        session.flush()
        for sample_id in (original.id, "sample-499", "sample-1000"):
            session.add(
                ReviewDecision(
                    task_id=task.id,
                    sample_id=sample_id,
                    scope_type="sample",
                    category="manual",
                    decision="approved_exclude",
                    source="human",
                )
            )
            session.add(
                Evidence(
                    task_id=task.id,
                    sample_id=sample_id,
                    code="aesthetic_score",
                    source="test",
                    value_json=0.8,
                    severity="info",
                    algorithm_version="test",
                )
            )

    # Force the portable SQLite limit: the old single IN query exceeds it.
    @event.listens_for(database.engine, "connect")
    def limit_parameters(connection, _):
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    with database.read_session() as session:
        ids = tuple(session.scalars(select(Sample.id).where(Sample.task_id == task.id)))
        overlays = active_human_overlay_by_sample(session, task_id=task.id, sample_ids=ids + ids)
        assert len(overlays) == 3
        assert all(len(rows) == 1 for rows in overlays.values())
        evidence = EligibilityResolver._evidence_by_code(
            session, task_id=task.id, sample_ids=ids, codes=("aesthetic_score",)
        )
        assert set(evidence["aesthetic_score"]) == set(overlays)
        aesthetic = ExportRunPlanner._aesthetic_evidence(session, task.id, ids, threshold=0.5)
        assert set(aesthetic) == set(overlays)

    (tmp_path / "export").mkdir()
    service = ExportRunService(database)
    settings = dict(output_root=str(tmp_path / "export"), minimum_resolution=512)
    preview = service.preview(task.id, **settings)
    assert preview.included_count == 999
    assert preview.exclusion_counts["manual_exclude"] == 3
    run = service.complete_first_copy_export(
        task.id, preview_digest=preview.preview_digest, **settings
    )
    assert run.task_id == task.id
    assert run.preview_digest == preview.preview_digest
