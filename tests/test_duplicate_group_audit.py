from __future__ import annotations

from pathlib import Path

from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import (
    Evidence,
    ReviewDecision,
    Sample,
    Task,
)
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from fastapi.testclient import TestClient


def _seed_duplicate_audit_task(database, task_service: TaskService, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    task = task_service.create_task(
        name="duplicate audit",
        source_root=str(source),
        output_root=None,
        config=ComponentTaskConfigMaterializer().materialize(
            materialize_profile("general")["components"],
            profile="general",
            require_profile=True,
        ),
    )
    sample_ids: dict[str, str] = {}
    with database.write_session() as session:
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.EVIDENCE_REVIEW.value
        for name, artist_scope in (
            ("alpha", "artist-a"),
            ("bravo", "artist-b"),
            ("charlie", "artist-a"),
            ("delta", "artist-a"),
        ):
            sample = Sample(
                task_id=task.id,
                relative_path=f"{artist_scope}/{name}.png",
                source_size=1,
                source_mtime_ns=1,
                source_sha256=f"{len(sample_ids) + 1:064x}",
                pixel_sha256=f"{len(sample_ids) + 11:064x}",
                media_kind="image",
                artist_scope=artist_scope,
                scan_state="valid",
                encoded_width={"alpha": 1024, "bravo": 768, "charlie": 512, "delta": 512}[name],
                encoded_height={"alpha": 1024, "bravo": 768, "charlie": 512, "delta": 512}[name],
                display_width={"alpha": 1024, "bravo": 768, "charlie": 512, "delta": 512}[name],
                display_height={"alpha": 1024, "bravo": 768, "charlie": 512, "delta": 512}[name],
                frame_count=1,
                is_animated=False,
                exif_orientation=1,
                extracted_frame_path=None,
                export_requires_render=False,
                phash=None,
                colorhash=None,
                scan_algorithm_version="test",
            )
            session.add(sample)
            session.flush()
            sample_ids[name] = sample.id

        def evidence(name: str, code: str, group_key: str | None, score: float | None) -> None:
            metadata = {} if group_key is None else {"group_key": group_key}
            session.add(
                Evidence(
                    task_id=task.id,
                    sample_id=sample_ids[name],
                    code=code,
                    source="duplicate_audit_v1",
                    value_json=group_key or "missing",
                    threshold_json=None,
                    value_number=score,
                    threshold_number=None,
                    metadata_json=metadata,
                    severity="medium",
                    review_only=True,
                    bbox_json=None,
                    algorithm_version="test",
                )
            )

        evidence("alpha", "duplicate_exact", "exact-a", 0.5)
        evidence("alpha", "duplicate_exact", "exact-a", 0.9)
        evidence("bravo", "duplicate_exact", "exact-a", 0.7)
        evidence("charlie", "duplicate_exact", "exact-z", None)
        evidence("delta", "duplicate_exact", "exact-z", None)
        evidence("delta", "duplicate_exact", "", None)
        evidence("alpha", "duplicate_visual", "visual-a", 0.8)
        evidence("bravo", "duplicate_visual", "visual-a", 0.8)
        evidence("charlie", "duplicate_semantic", "semantic-a", 0.6)
        evidence("delta", "duplicate_semantic", "semantic-a", 0.6)
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id=sample_ids["bravo"],
                scope_type="sample",
                scope_id=sample_ids["bravo"],
                category="curated:exact_duplicate",
                decision="approved_exclude",
                source="human",
                context_json={},
                is_active=True,
            )
        )
    return task, sample_ids


def test_duplicate_group_audit_is_stable_complete_and_uses_canonical_dimensions(
    database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task, sample_ids = _seed_duplicate_audit_task(database, task_service, tmp_path)
    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        models_root=tmp_path / "api-models",
    )

    with TestClient(app) as client:
        response = client.get(
            f"/api/tasks/{task.id}/reviews/duplicates/audit?evidence_type=exact_duplicate&offset=0&limit=1"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert payload["unresolved"] == 1
        assert [item["group_key"] for item in payload["items"]] == ["exact-a"]
        group = payload["items"][0]
        assert group["member_count"] == 2
        assert group["pending"] == 1
        assert group["approved_keep"] == 0
        assert group["approved_exclude"] == 1
        assert group["effective_retained_count"] == 1
        assert [item["relative_path"] for item in group["members"]] == [
            "artist-a/alpha.png",
            "artist-b/bravo.png",
        ]
        assert group["members"][0]["score"] == 0.9
        assert group["members"][0]["resolutions"] == [512, 768, 1024]
        assert group["members"][0]["pixel_area"] == 1024 * 1024
        assert group["members"][1]["resolutions"] == [512, 768]
        assert group["members"][1]["pixel_area"] == 768 * 768
        assert group["members"][1]["decision"] == "approved_exclude"
        assert group["members"][1]["decision_source"] == "human"
        assert all(item["review_eligible"] is True for item in group["members"])

        next_page = client.get(
            f"/api/tasks/{task.id}/reviews/duplicates/audit?evidence_type=exact_duplicate&offset=1&limit=1"
        )
        assert [item["group_key"] for item in next_page.json()["items"]] == ["exact-z"]

        scoped = client.get(
            f"/api/tasks/{task.id}/reviews/duplicates/audit?evidence_type=exact_duplicate&offset=0&limit=10&folder=artist-b"
        )
        assert scoped.json()["total"] == 1
        assert [item["sample_id"] for item in scoped.json()["items"][0]["members"]] == [
            sample_ids["alpha"],
            sample_ids["bravo"],
        ]

        for evidence_type, group_key in (
            ("visual_duplicate", "visual-a"),
            ("semantic_duplicate", "semantic-a"),
        ):
            typed = client.get(
                f"/api/tasks/{task.id}/reviews/duplicates/audit?evidence_type={evidence_type}&offset=0&limit=10"
            )
            assert typed.status_code == 200
            assert [item["group_key"] for item in typed.json()["items"]] == [group_key]

        legacy = client.get(
            f"/api/tasks/{task.id}/reviews/curated?evidence_type=exact_duplicate&offset=0&limit=10"
        )
        assert legacy.status_code == 200
        decided = client.post(
            f"/api/tasks/{task.id}/reviews/curated/decisions",
            json={
                "decision": "approved_keep",
                "evidence_type": "exact_duplicate",
                "sample_ids": [sample_ids["alpha"]],
            },
        )
        assert decided.status_code == 200
