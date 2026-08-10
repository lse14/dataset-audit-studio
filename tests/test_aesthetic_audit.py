from __future__ import annotations

from pathlib import Path

from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.enums import ReviewState, TaskStatus
from dataset_audit_studio.database.models import (
    Evidence,
    ReviewDecision,
    Sample,
    Task,
)
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from dataset_audit_studio.scoring.assets import EVIDENCE_SOURCES, PREPROCESSING_VERSIONS
from dataset_audit_studio.scoring.config import ScoringConfig
from fastapi.testclient import TestClient


def _seed_aesthetic_audit_task(database, task_service: TaskService, tmp_path: Path):
    source = tmp_path / "aesthetic-source"
    source.mkdir()
    components = materialize_profile("general")["components"]
    components["score.aesthetic_domain"]["enabled"] = True
    components["export.dataset"]["config"]["aesthetic_minimum"] = 3.0
    task = task_service.create_task(
        name="aesthetic audit",
        source_root=str(source),
        output_root=None,
        config=ComponentTaskConfigMaterializer().materialize(
            components, profile="general", require_profile=True
        ),
    )
    scoring = ScoringConfig.from_task_config(task.config)
    identity = {
        "source": EVIDENCE_SOURCES["aesthetic"],
        "model_id": scoring.aesthetic.model_id,
        "config_hash": scoring.inference_config_hash("aesthetic"),
        "algorithm_version": PREPROCESSING_VERSIONS["aesthetic"],
    }
    samples = {
        "low": ("artist-a/low.png", "artist-a"),
        "boundary": ("artist-a/boundary.png", "artist-a"),
        "high": ("artist-a/high.png", "artist-a"),
        "ordinary": ("artist-a/ordinary.png", "artist-a"),
        "nonfinite": ("artist-b/nonfinite.png", "artist-b"),
        "out": ("artist-b/out.png", "artist-b"),
        "mismatch": ("artist-b/mismatch.png", "artist-b"),
        "ambiguous": ("artist-c/ambiguous.png", "artist-c"),
        "missing": ("artist-c/missing.png", "artist-c"),
    }
    sample_ids: dict[str, str] = {}
    with database.write_session() as session:
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.EVIDENCE_REVIEW.value
        for name, (relative_path, artist_scope) in samples.items():
            sample_path = source / relative_path
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            sample_path.write_bytes(name.encode())
            sample = Sample(
                task_id=task.id,
                relative_path=relative_path,
                source_size=1,
                source_mtime_ns=1,
                source_sha256=f"{len(sample_ids) + 1:064x}",
                pixel_sha256=f"{len(sample_ids) + 11:064x}",
                media_kind="image",
                artist_scope=artist_scope,
                scan_state="valid",
                encoded_width=64,
                encoded_height=64,
                display_width=64,
                display_height=64,
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

        def evidence(name: str, value: object, *, identity_override: dict[str, str] | None = None):
            provenance = dict(identity)
            if identity_override:
                provenance.update(identity_override)
            numeric = (
                value
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None
            )
            session.add(
                Evidence(
                    task_id=task.id,
                    sample_id=sample_ids[name],
                    code="aesthetic_score",
                    source=provenance["source"],
                    value_json=value,
                    threshold_json=None,
                    value_number=numeric,
                    threshold_number=None,
                    metadata_json={
                        "model_id": provenance["model_id"],
                        "config_hash": provenance["config_hash"],
                    },
                    severity="info",
                    review_only=True,
                    bbox_json=None,
                    algorithm_version=provenance["algorithm_version"],
                )
            )

        evidence("low", 1.49)
        evidence("boundary", 1.5)
        evidence("high", 5.0)
        evidence("ordinary", 4.0)
        evidence("nonfinite", float("nan"))
        evidence("out", 5.1)
        evidence("mismatch", 4.0, identity_override={"source": "old-aesthetic"})
        evidence("ambiguous", 3.0)
        evidence("ambiguous", 4.0)
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id=sample_ids["high"],
                scope_type="sample",
                scope_id=sample_ids["high"],
                category="curated:aesthetic",
                decision=ReviewState.APPROVED_KEEP.value,
                source="human",
                context_json={"test": True},
                is_active=True,
            )
        )
    return task_service.get_task(task.id), sample_ids


def test_aesthetic_audit_is_full_read_model_with_bins_invalid_counts_and_overlay(
    database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task, sample_ids = _seed_aesthetic_audit_task(database, task_service, tmp_path)
    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        models_root=tmp_path / "models",
    )
    with TestClient(app) as client:
        response = client.get(
            f"/api/tasks/{task.id}/reviews/aesthetic/audit",
            params={"offset": 0, "limit": 20},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 9
    assert payload["bucket_counts"] == {
        "1.0": 1,
        "1.5": 1,
        "2.0": 0,
        "2.5": 0,
        "3.0": 0,
        "3.5": 0,
        "4.0": 1,
        "4.5": 0,
        "5.0": 1,
    }
    assert payload["invalid_counts"] == {
        "missing": 1,
        "non_finite": 1,
        "out_of_range": 1,
        "provenance_mismatch": 1,
        "ambiguous": 1,
    }
    assert payload["pending"] == 7
    assert payload["approved_keep"] == 1
    assert payload["approved_exclude"] == 0
    by_path = {item["relative_path"]: item for item in payload["items"]}
    assert by_path["artist-a/low.png"]["bucket"] == 1.0
    assert by_path["artist-a/boundary.png"]["bucket"] == 1.5
    assert by_path["artist-a/high.png"]["bucket"] == 5.0
    assert by_path["artist-a/high.png"]["decision"] == "approved_keep"
    assert by_path["artist-a/high.png"]["review_eligible"] is True
    assert by_path["artist-c/missing.png"]["reason_code"] == "missing"
    assert by_path["artist-c/missing.png"]["review_eligible"] is True
    assert by_path["artist-b/nonfinite.png"]["reason_code"] == "non_finite"
    assert by_path["artist-b/out.png"]["reason_code"] == "out_of_range"
    assert by_path["artist-b/mismatch.png"]["reason_code"] == "provenance_mismatch"
    assert by_path["artist-c/ambiguous.png"]["reason_code"] == "ambiguous"
    assert set(by_path["artist-a/low.png"]) == {
        "sample_id",
        "relative_path",
        "artist_scope",
        "score",
        "bucket",
        "reason_code",
        "review_eligible",
        "decision",
        "decision_source",
    }
    assert sample_ids["high"]


def test_aesthetic_audit_filters_exact_folder_bucket_and_paginates(
    database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task, _sample_ids = _seed_aesthetic_audit_task(database, task_service, tmp_path)
    app = create_app(database_path=database.path, enforce_runtime=False, start_worker=False)
    with TestClient(app) as client:
        scoped = client.get(
            f"/api/tasks/{task.id}/reviews/aesthetic/audit",
            params={"folder": "artist-a", "bucket": "1.0", "offset": 0, "limit": 1},
        )
        second_page = client.get(
            f"/api/tasks/{task.id}/reviews/aesthetic/audit",
            params={"folder": "artist-a", "offset": 1, "limit": 1},
        )
    assert scoped.status_code == 200
    assert scoped.json()["total"] == 1
    assert scoped.json()["items"][0]["relative_path"] == "artist-a/low.png"
    assert second_page.json()["items"][0]["relative_path"] == "artist-a/boundary.png"


def test_aesthetic_audit_only_reuses_curated_post_for_candidates_or_active_overlays(
    database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task, sample_ids = _seed_aesthetic_audit_task(database, task_service, tmp_path)
    app = create_app(database_path=database.path, enforce_runtime=False, start_worker=False)
    with TestClient(app) as client:
        ordinary = client.post(
            f"/api/tasks/{task.id}/reviews/curated/decisions",
            json={
                "evidence_type": "aesthetic",
                "decision": "approved_exclude",
                "sample_ids": [sample_ids["ordinary"]],
            },
        )
        overlay = client.post(
            f"/api/tasks/{task.id}/reviews/curated/decisions",
            json={
                "evidence_type": "aesthetic",
                "decision": "approved_exclude",
                "sample_ids": [sample_ids["high"]],
            },
        )
        candidate = client.post(
            f"/api/tasks/{task.id}/reviews/curated/decisions",
            json={
                "evidence_type": "aesthetic",
                "decision": "approved_exclude",
                "sample_ids": [sample_ids["low"]],
            },
        )
    assert ordinary.status_code == 200
    assert ordinary.json()["selected"] == 0
    assert overlay.status_code == 200
    assert overlay.json()["selected"] == 1
    assert candidate.status_code == 200
    assert candidate.json()["selected"] == 1
