import json

from dataset_audit_studio.database.models import Evidence, ReviewDecision, Sample, Task
from dataset_audit_studio.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from test_task_service import _task_config


def test_risk_selection_matches_filters_supports_500_and_reversible_batches(
    database, task_service, tmp_path,
):
    task = task_service.create_task(
        name="bulk risks", source_root=str(tmp_path), output_root=None, config=_task_config(),
    )
    with database.write_session() as session:
        session.get(Task, task.id).status = "evidence_review"
        session.execute(insert(Sample), [dict(
            id=f"s-{i:05}", task_id=task.id, relative_path=f"{i}.png",
            artist_scope="A" if i < 5101 else "B", source_size=1, source_mtime_ns=1,
            source_sha256="a" * 64, media_kind="image", scan_state="valid",
            scan_algorithm_version="test",
        ) for i in range(5103)])
        session.execute(insert(Evidence), [dict(
            sample_id=f"s-{i:05}", task_id=task.id, code="rgb_entropy", source="test",
            value_json=1.0, threshold_json=0.5,
            severity="medium" if i < 5102 else "info", review_only=True,
            algorithm_version="test", metadata_json={},
        ) for i in range(5103)])
        session.add(Evidence(
            sample_id="s-00002", task_id=task.id, code="watermark_probability", source="test",
            value_json=1.0, threshold_json=0.5,
            severity="high", review_only=True, algorithm_version="test", metadata_json={},
        ))
        for i, decision in enumerate(("approved_keep", "approved_exclude")):
            session.add(ReviewDecision(
                task_id=task.id, sample_id=f"s-{i:05}", scope_type="sample",
                scope_id=f"s-{i:05}", category="curated:risk", decision=decision,
                source="human", context_json={}, is_active=True,
            ))
    app = create_app(database_path=database.path, enforce_runtime=False, start_worker=False,
                     models_root=tmp_path / "models")
    base = f"/api/tasks/{task.id}"
    filters = {"folder": "A", "code": "rgb_entropy", "severity": "medium"}
    with TestClient(app) as client:
        response = client.get(f"{base}/risk-sample-ids", params=filters)
        assert response.status_code == 200
        assert len(response.json()) == 5101
        assert len(set(response.json())) == 5101
        pending = client.get(f"{base}/risk-sample-ids", params={
            **filters, "decision": "pending_review",
        }).json()
        assert len(pending) == 5099 and "s-00000" not in pending and "s-00001" not in pending
        page = client.get(f"{base}/risk-samples", params={**filters, "limit": 500})
        assert page.status_code == 200 and len(page.json()["items"]) == 500
        assert page.json()["total"] == len(response.json())
        assert client.get(f"{base}/risk-samples", params={"limit": 501}).status_code == 422
        assert client.get(f"{base}/risk-sample-ids", params={"severity": "info"}).json() == []
        watermark_ids = client.get(
            f"{base}/risk-sample-ids", params={"code": "watermark_probability"},
        ).json()
        assert watermark_ids == ["s-00002"]
        batch = pending[:500]
        for decision in ("approved_exclude", "approved_keep"):
            result = client.post(f"{base}/reviews/curated/decisions", json={
                "evidence_type": "risk", "decision": decision, "sample_ids": batch,
            })
            assert result.status_code == 200
            assert result.json()["changed"] == 500
        excluded = client.get(f"{base}/risk-sample-ids", params={
            **filters, "decision": "approved_exclude",
        }).json()
        assert excluded == ["s-00001"]
    with database.read_session() as session:
        records = session.scalars(select(ReviewDecision).where(
            ReviewDecision.sample_id == batch[0],
        )).all()
        assert len(records) == 2 and sum(item.is_active for item in records) == 1
        assert all(len(json.dumps(item.context_json)) < 1000 for item in records)
