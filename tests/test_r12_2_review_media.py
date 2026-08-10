from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import Evidence, ExportRun, ReviewDecision, Sample, Task
from dataset_audit_studio.export_runs.service import ExportRunService
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from dataset_audit_studio.scoring.assets import EVIDENCE_SOURCES, PREPROCESSING_VERSIONS
from dataset_audit_studio.scoring.config import ScoringConfig
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_config() -> dict:
    components = materialize_profile("general")["components"]
    components["score.aesthetic_domain"]["enabled"] = True
    components["export.dataset"]["config"]["aesthetic_minimum"] = 3.0
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def _sample(
    *,
    task_id: str,
    source: Path,
    relative_path: str,
    artist_scope: str,
    media_kind: str = "image",
    extracted_frame_path: str | None = None,
) -> Sample:
    path = source.joinpath(*Path(relative_path).parts)
    stat = path.stat()
    digest = _sha256(path)
    return Sample(
        task_id=task_id,
        relative_path=relative_path,
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        source_sha256=digest,
        pixel_sha256=digest,
        media_kind=media_kind,
        artist_scope=artist_scope,
        scan_state="valid",
        encoded_width=512,
        encoded_height=512,
        display_width=512,
        display_height=512,
        frame_count=1,
        is_animated=media_kind != "image",
        exif_orientation=1,
        extracted_frame_path=extracted_frame_path,
        export_requires_render=media_kind != "image",
        phash=None,
        colorhash=None,
        scan_algorithm_version="test",
    )


def _seed_completed_audit_task(
    database,
    task_service: TaskService,
    tmp_path: Path,
    *,
    alpha_decision: str = "approved_exclude",
):
    source = tmp_path / "source"
    source.mkdir()
    paths = {
        "alpha": ("artist-a/alpha.png", "artist-a"),
        "bravo": ("artist-a/bravo.png", "artist-a"),
        "charlie": ("artist-b/charlie.png", "artist-b"),
        "delta": ("artist-b/delta.png", "artist-b"),
        "echo": ("artist-c/echo.png", "artist-c"),
    }
    for index, (relative_path, _scope) in enumerate(paths.values()):
        path = source.joinpath(*Path(relative_path).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), (index * 30, 40, 120)).save(path)

    task = task_service.create_task(
        name="r12.2 completed audit",
        source_root=str(source),
        output_root=None,
        config=_task_config(),
    )
    scoring = ScoringConfig.from_task_config(task.config)
    aesthetic_identity = {
        "source": EVIDENCE_SOURCES["aesthetic"],
        "model_id": scoring.aesthetic.model_id,
        "config_hash": scoring.inference_config_hash("aesthetic"),
        "algorithm_version": PREPROCESSING_VERSIONS["aesthetic"],
    }
    sample_ids: dict[str, str] = {}
    with database.write_session() as session:
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.COMPLETED.value
        for name, (relative_path, scope) in paths.items():
            sample = _sample(
                task_id=task.id,
                source=source,
                relative_path=relative_path,
                artist_scope=scope,
            )
            session.add(sample)
            session.flush()
            sample_ids[name] = sample.id
            session.add_all(
                (
                    Evidence(
                        task_id=task.id,
                        sample_id=sample.id,
                        code="watermark_probability",
                        source="r12.2-test",
                        value_json=0.9,
                        threshold_json=0.5,
                        value_number=0.9,
                        threshold_number=0.5,
                        metadata_json={},
                        severity="high",
                        review_only=True,
                        bbox_json=None,
                        algorithm_version="test",
                    ),
                    Evidence(
                        task_id=task.id,
                        sample_id=sample.id,
                        code="artist_style_score",
                        source="artist_style_v1",
                        value_json=0.8,
                        threshold_json=0.3,
                        value_number=0.8,
                        threshold_number=0.3,
                        metadata_json={},
                        severity="high",
                        review_only=True,
                        bbox_json=None,
                        algorithm_version="test",
                    ),
                    Evidence(
                        task_id=task.id,
                        sample_id=sample.id,
                        code="aesthetic_score",
                        source=aesthetic_identity["source"],
                        value_json=1.0,
                        threshold_json=None,
                        value_number=1.0,
                        threshold_number=None,
                        metadata_json={
                            "model_id": aesthetic_identity["model_id"],
                            "config_hash": aesthetic_identity["config_hash"],
                        },
                        severity="info",
                        review_only=True,
                        bbox_json=None,
                        algorithm_version=aesthetic_identity["algorithm_version"],
                    ),
                )
            )
        for name, group_key in (
            ("alpha", "group-a"),
            ("bravo", "group-a"),
            ("charlie", "group-b"),
            ("delta", "group-b"),
        ):
            session.add(
                Evidence(
                    task_id=task.id,
                    sample_id=sample_ids[name],
                    code="duplicate_exact",
                    source="r12.2-test",
                    value_json=group_key,
                    threshold_json=None,
                    value_number=0.9,
                    threshold_number=None,
                    metadata_json={"group_key": group_key},
                    severity="medium",
                    review_only=True,
                    bbox_json=None,
                    algorithm_version="test",
                )
            )
        session.add_all(
            (
                ReviewDecision(
                    task_id=task.id,
                    sample_id=sample_ids["alpha"],
                    scope_type="sample",
                    scope_id=sample_ids["alpha"],
                    category="curated:risk",
                    decision=alpha_decision,
                    source="human",
                    context_json={"test": "prior"},
                    supersedes_id=None,
                    is_active=True,
                ),
                ReviewDecision(
                    task_id=task.id,
                    sample_id=sample_ids["bravo"],
                    scope_type="sample",
                    scope_id=sample_ids["bravo"],
                    category="style_outlier",
                    decision="approved_exclude",
                    source="human",
                    context_json={},
                    supersedes_id=None,
                    is_active=True,
                ),
                ReviewDecision(
                    task_id=task.id,
                    sample_id=sample_ids["delta"],
                    scope_type="sample",
                    scope_id=sample_ids["delta"],
                    category="curated:aesthetic",
                    decision="approved_exclude",
                    source="human",
                    context_json={},
                    supersedes_id=None,
                    is_active=True,
                ),
                ReviewDecision(
                    task_id=task.id,
                    sample_id=sample_ids["echo"],
                    scope_type="sample",
                    scope_id=sample_ids["echo"],
                    category="style_outlier",
                    decision="approved_keep",
                    source="human",
                    context_json={},
                    supersedes_id=None,
                    is_active=True,
                ),
            )
        )
    return task_service.get_task(task.id), sample_ids


def test_completed_decisions_supersede_without_mutating_frozen_export_or_history(
    database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task, sample_ids = _seed_completed_audit_task(database, task_service, tmp_path)
    export_service = ExportRunService(database)
    frozen_output = tmp_path / "frozen-output"
    frozen_output.mkdir()
    before = export_service.preview(
        task.id,
        output_root=str(frozen_output),
        minimum_resolution=512,
    )
    run = export_service.create(
        task.id,
        output_root=str(frozen_output),
        minimum_resolution=512,
        preview_digest=before.preview_digest,
    )
    with database.read_session() as session:
        persisted = session.get(ExportRun, run.id)
        assert persisted is not None
        frozen_snapshot = copy.deepcopy(persisted.input_snapshot_json)
        evidence_before = session.scalars(
            select(Evidence).where(Evidence.task_id == task.id)
        ).all()
        evidence_ids = [row.id for row in evidence_before]
        task_before = session.get(Task, task.id)
        assert task_before is not None
        task_status = task_before.status
        config_revision = task_before.current_config_revision

    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        project_root=tmp_path / "project",
    )
    with TestClient(app) as client:
        decided = client.post(
            f"/api/tasks/{task.id}/reviews/curated/decisions",
            json={
                "evidence_type": "risk",
                "decision": "approved_keep",
                "sample_ids": [sample_ids["alpha"]],
            },
        )
    assert decided.status_code == 200
    assert decided.json() == {
        "selected": 1,
        "changed": 1,
        "decision": "approved_keep",
    }

    refreshed_output = tmp_path / "refreshed-output"
    refreshed_output.mkdir()
    after = export_service.preview(
        task.id,
        output_root=str(refreshed_output),
        minimum_resolution=512,
    )
    assert after.preview_digest != before.preview_digest
    assert after.included_count == before.included_count + 1
    with database.read_session() as session:
        decisions = session.scalars(
            select(ReviewDecision)
            .where(
                ReviewDecision.task_id == task.id,
                ReviewDecision.sample_id == sample_ids["alpha"],
            )
            .order_by(ReviewDecision.created_at, ReviewDecision.id)
        ).all()
        persisted = session.get(ExportRun, run.id)
        task_after = session.get(Task, task.id)
        assert persisted is not None
        assert task_after is not None
        assert persisted.input_snapshot_json == frozen_snapshot
        assert task_after.status == task_status
        assert task_after.current_config_revision == config_revision
        current_evidence_ids = [
            row.id for row in session.scalars(select(Evidence).where(Evidence.task_id == task.id))
        ]
        assert current_evidence_ids == evidence_ids
    assert len(decisions) == 2
    assert decisions[0].decision == "approved_exclude"
    assert decisions[0].is_active is False
    assert decisions[1].decision == "approved_keep"
    assert decisions[1].is_active is True
    assert decisions[1].supersedes_id == decisions[0].id


def test_audit_status_filters_apply_before_totals_and_group_pagination(
    database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task, sample_ids = _seed_completed_audit_task(
        database,
        task_service,
        tmp_path,
        alpha_decision="approved_keep",
    )
    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        project_root=tmp_path / "project",
    )
    with TestClient(app) as client:
        risk_keep = client.get(
            f"/api/tasks/{task.id}/risk-samples",
            params={
                "decision": "approved_keep",
                "folder": "artist-c",
                "code": "watermark_probability",
                "severity": "high",
            },
        )
        risk_pending = client.get(
            f"/api/tasks/{task.id}/risk-samples",
            params={"decision": "pending_review", "folder": "artist-b"},
        )
        style_exclude = client.get(
            f"/api/tasks/{task.id}/reviews/style/audit",
            params={"decision": "approved_exclude", "folder": "artist-a"},
        )
        style_pending = client.get(
            f"/api/tasks/{task.id}/reviews/style/audit",
            params={"decision": "pending_review", "folder": "artist-b"},
        )
        aesthetic_exclude = client.get(
            f"/api/tasks/{task.id}/reviews/aesthetic/audit",
            params={
                "decision": "approved_exclude",
                "folder": "artist-b",
                "bucket": 1.0,
            },
        )
        aesthetic_pending = client.get(
            f"/api/tasks/{task.id}/reviews/aesthetic/audit",
            params={"decision": "pending_review", "folder": "artist-b"},
        )
        duplicate_keep = client.get(
            f"/api/tasks/{task.id}/reviews/duplicates/audit",
            params={
                "evidence_type": "exact_duplicate",
                "decision": "approved_keep",
                "offset": 0,
                "limit": 1,
            },
        )
        duplicate_exclude = client.get(
            f"/api/tasks/{task.id}/reviews/duplicates/audit",
            params={
                "evidence_type": "exact_duplicate",
                "decision": "approved_exclude",
                "offset": 0,
                "limit": 1,
            },
        )
        duplicate_pending = client.get(
            f"/api/tasks/{task.id}/reviews/duplicates/audit",
            params={
                "evidence_type": "exact_duplicate",
                "decision": "pending_review",
                "folder": "artist-b",
                "offset": 0,
                "limit": 1,
            },
        )

    assert risk_keep.status_code == 200
    assert risk_keep.json()["total"] == 1
    assert risk_keep.json()["items"][0]["sample_id"] == sample_ids["echo"]
    assert risk_pending.json()["total"] == 1
    assert risk_pending.json()["items"][0]["sample_id"] == sample_ids["charlie"]
    assert style_exclude.json()["total"] == 1
    assert style_exclude.json()["items"][0]["sample_id"] == sample_ids["bravo"]
    assert style_pending.json()["total"] == 2
    assert {item["sample_id"] for item in style_pending.json()["items"]} == {
        sample_ids["charlie"],
        sample_ids["delta"],
    }
    assert aesthetic_exclude.json()["total"] == 1
    assert aesthetic_exclude.json()["items"][0]["sample_id"] == sample_ids["delta"]
    assert aesthetic_pending.json()["total"] == 1
    assert aesthetic_pending.json()["items"][0]["sample_id"] == sample_ids["charlie"]
    assert duplicate_keep.json()["total"] == 1
    assert duplicate_keep.json()["items"][0]["group_key"] == "group-a"
    assert [item["sample_id"] for item in duplicate_keep.json()["items"][0]["members"]] == [
        sample_ids["alpha"],
        sample_ids["bravo"],
    ]
    assert duplicate_exclude.json()["total"] == 2
    assert duplicate_exclude.json()["items"][0]["member_count"] == 2
    assert duplicate_pending.json()["total"] == 1
    assert duplicate_pending.json()["items"][0]["group_key"] == "group-b"
    assert len(duplicate_pending.json()["items"][0]["members"]) == 2


def test_media_endpoint_serves_original_or_extracted_frame_and_fails_closed(
    database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "media-source"
    source.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    static = source / "static.png"
    stale = source / "stale.png"
    mismatch = source / "mismatch.png"
    video = source / "clip.mp4"
    for path, color in ((static, "red"), (stale, "green"), (mismatch, "blue")):
        Image.new("RGB", (32, 32), color).save(path)
    video.write_bytes(b"r12.2 video source")
    frame = project / "data" / "tasks" / "frames" / "clip.png"
    frame.parent.mkdir(parents=True)
    Image.new("RGB", (16, 8), "purple").save(frame)
    escaped = source.parent / "escaped.png"
    Image.new("RGB", (8, 8), "black").save(escaped)

    task = task_service.create_task(
        name="r12.2 media",
        source_root=str(source),
        output_root=None,
        config=_task_config(),
    )
    other = task_service.create_task(
        name="r12.2 other media",
        source_root=str(source),
        output_root=None,
        config=_task_config(),
    )
    with database.write_session() as session:
        static_sample = _sample(
            task_id=task.id,
            source=source,
            relative_path="static.png",
            artist_scope="__root__",
        )
        video_sample = _sample(
            task_id=task.id,
            source=source,
            relative_path="clip.mp4",
            artist_scope="__root__",
            media_kind="video",
            extracted_frame_path="data/tasks/frames/clip.png",
        )
        stale_sample = _sample(
            task_id=task.id,
            source=source,
            relative_path="stale.png",
            artist_scope="__root__",
        )
        mismatch_sample = _sample(
            task_id=task.id,
            source=source,
            relative_path="mismatch.png",
            artist_scope="__root__",
        )
        escape_sample = _sample(
            task_id=task.id,
            source=source,
            relative_path="../escaped.png",
            artist_scope="__root__",
        )
        foreign_sample = _sample(
            task_id=other.id,
            source=source,
            relative_path="static.png",
            artist_scope="__root__",
        )
        session.add_all(
            (
                static_sample,
                video_sample,
                stale_sample,
                mismatch_sample,
                escape_sample,
                foreign_sample,
            )
        )
        session.flush()
        mismatch_sample.source_sha256 = "0" * 64
        ids = {
            "static": static_sample.id,
            "video": video_sample.id,
            "stale": stale_sample.id,
            "mismatch": mismatch_sample.id,
            "escape": escape_sample.id,
            "foreign": foreign_sample.id,
        }

    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        project_root=project,
    )
    with TestClient(app) as client:
        original = client.get(f"/api/tasks/{task.id}/samples/{ids['static']}/media")
        extracted = client.get(f"/api/tasks/{task.id}/samples/{ids['video']}/media")
        Image.new("RGB", (33, 33), "white").save(stale)
        stale_response = client.get(f"/api/tasks/{task.id}/samples/{ids['stale']}/media")
        mismatch_response = client.get(
            f"/api/tasks/{task.id}/samples/{ids['mismatch']}/media"
        )
        escape_response = client.get(f"/api/tasks/{task.id}/samples/{ids['escape']}/media")
        with database.write_session() as session:
            escape_row = session.get(Sample, ids["escape"])
            assert escape_row is not None
            escape_row.relative_path = "C:/outside.png"
        absolute_response = client.get(f"/api/tasks/{task.id}/samples/{ids['escape']}/media")
        with database.write_session() as session:
            escape_row = session.get(Sample, ids["escape"])
            assert escape_row is not None
            escape_row.relative_path = "..\\escaped.png"
        backslash_response = client.get(f"/api/tasks/{task.id}/samples/{ids['escape']}/media")
        foreign_response = client.get(f"/api/tasks/{task.id}/samples/{ids['foreign']}/media")
        with database.write_session() as session:
            video_row = session.get(Sample, ids["video"])
            assert video_row is not None
            video_row.extracted_frame_path = "../escaped.png"
        frame_escape_response = client.get(f"/api/tasks/{task.id}/samples/{ids['video']}/media")

    assert original.status_code == 200
    assert original.headers["content-type"] == "image/png"
    assert original.content == static.read_bytes()
    assert extracted.status_code == 200
    assert extracted.headers["content-type"] == "image/png"
    assert extracted.content == frame.read_bytes()
    assert not list((project / "data" / "tasks").rglob("thumbnails"))
    for response in (
        stale_response,
        mismatch_response,
        escape_response,
        absolute_response,
        backslash_response,
        foreign_response,
        frame_escape_response,
    ):
        assert response.status_code in {404, 409}
        assert str(source.resolve()) not in response.text


def test_media_endpoint_serves_webp_when_mime_inference_is_missing(
    database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "media-source"
    source.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    webp = source / "sample.webp"
    Image.new("RGB", (24, 16), "orange").save(webp, format="WEBP")

    task = task_service.create_task(
        name="webp media",
        source_root=str(source),
        output_root=None,
        config=_task_config(),
    )
    with database.write_session() as session:
        sample = _sample(
            task_id=task.id,
            source=source,
            relative_path="sample.webp",
            artist_scope="__root__",
        )
        session.add(sample)
        session.flush()
        sample_id = sample.id

    monkeypatch.setattr(
        "dataset_audit_studio.workspace.file_access.mimetypes.guess_type",
        lambda *_args, **_kwargs: (None, None),
    )
    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        project_root=project,
    )
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task.id}/samples/{sample_id}/media")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.content == webp.read_bytes()
