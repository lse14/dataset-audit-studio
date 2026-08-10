from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from dataset_audit_studio.adapters.dataset_workspace import DatasetWorkspaceRepository
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.enums import ArtifactState, TaskStatus
from dataset_audit_studio.database.models import (
    Artifact,
    ClusterMembership,
    ClusterNode,
    Evidence,
    ReviewDecision,
    Sample,
    Task,
)
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from dataset_audit_studio.workspace.service import WorkspaceService
from dataset_audit_studio.workspace.types import DirectoryListingView
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event, func, select


def _task_config() -> dict:
    return ComponentTaskConfigMaterializer().materialize(
        materialize_profile("general")["components"],
        profile="general",
        require_profile=True,
    )


def _create_task(app, *, name: str, source_root: Path, output_root: Path | None = None) -> dict:
    task = TaskService(app.state.database).create_task(
        name=name,
        source_root=str(source_root),
        output_root=str(output_root) if output_root is not None else None,
        config=_task_config(),
    )
    return {
        "id": task.id,
        "current_config_revision": task.current_config_revision,
        "config_hash": task.config_hash,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sample_row(
    *,
    task_id: str,
    source_root: Path,
    relative_path: str,
    artist_scope: str,
    size: int = 48,
) -> Sample:
    path = source_root.joinpath(*Path(relative_path).parts)
    stat = path.stat()
    digest = _sha256(path)
    return Sample(
        task_id=task_id,
        relative_path=relative_path,
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        source_sha256=digest,
        pixel_sha256=digest,
        media_kind="image",
        artist_scope=artist_scope,
        scan_state="valid",
        encoded_width=size,
        encoded_height=size,
        display_width=size,
        display_height=size,
        frame_count=1,
        is_animated=False,
        exif_orientation=1,
        extracted_frame_path=None,
        export_requires_render=False,
        phash=None,
        colorhash=None,
        scan_algorithm_version="test",
    )


def test_workspace_file_access_facades_delegate_to_injected_instance(
    database,
    tmp_path: Path,
) -> None:
    expected_thumbnail = tmp_path / "thumbnail.jpg"
    expected_directories = DirectoryListingView(
        current="C:\\dataset",
        parent="C:\\",
        entries=(),
    )

    class RecordingFileAccess:
        def __init__(self) -> None:
            self.thumbnail_calls: list[tuple[str, str, int]] = []
            self.directories_calls: list[str | None] = []

        def thumbnail(self, task_id: str, sample_id: str, *, size: int) -> Path:
            self.thumbnail_calls.append((task_id, sample_id, size))
            return expected_thumbnail

        def directories(self, raw_path: str | None) -> DirectoryListingView:
            self.directories_calls.append(raw_path)
            return expected_directories

    fake = RecordingFileAccess()
    service = WorkspaceService(database, project_root=tmp_path, file_access=fake)

    thumbnail = service.thumbnail("task-123", "sample-456", size=128)
    directories = service.directories("C:\\dataset")

    assert service.file_access is fake
    assert thumbnail is expected_thumbnail
    assert directories is expected_directories
    assert fake.thumbnail_calls == [("task-123", "sample-456", 128)]
    assert fake.directories_calls == ["C:\\dataset"]


def test_workspace_overview_clusters_risks_thumbnail_and_directory_browser(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    artist = source / "artist"
    artist.mkdir(parents=True)
    image_path = artist / "sample.png"
    Image.new("RGBA", (320, 160), (200, 20, 30, 180)).save(image_path)
    source_digest = _sha256(image_path)
    project = tmp_path / "project"
    project.mkdir()
    app = create_app(
        database_path=tmp_path / "workspace.db",
        enforce_runtime=False,
        project_root=project,
    )

    with TestClient(app) as client:
        task = _create_task(
            app,
            name="workspace",
            source_root=source,
            output_root=tmp_path / "output",
        )
        stat = image_path.stat()
        with app.state.database.write_session() as session:
            sample = Sample(
                task_id=task["id"],
                relative_path="artist/sample.png",
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=source_digest,
                pixel_sha256=source_digest,
                media_kind="image",
                artist_scope="artist",
                scan_state="valid",
                encoded_width=1216,
                encoded_height=1216,
                display_width=1216,
                display_height=1216,
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
            session.add(
                ClusterNode(
                    task_id=task["id"],
                    parent_id=None,
                    cluster_key="artist:leaf:0",
                    scope_kind="artist",
                    scope_id="artist",
                    level=0,
                    label=None,
                    size=1,
                    centroid_artifact_id=None,
                    metadata_json={
                        "is_leaf": True,
                        "representative_sample_id": sample.id,
                    },
                )
            )
            session.add(
                Evidence(
                    task_id=task["id"],
                    sample_id=sample.id,
                    code="watermark_probability",
                    source="watermark_siglip2",
                    value_json=0.72,
                    threshold_json=0.5,
                    value_number=0.72,
                    threshold_number=0.5,
                    metadata_json={"label": "watermark"},
                    severity="medium",
                    review_only=True,
                    bbox_json=[0.1, 0.2, 0.3, 0.4],
                    algorithm_version="test",
                )
            )
            session.add(
                Artifact(
                    task_id=task["id"],
                    sample_id=None,
                    kind="test",
                    phase="test",
                    cache_key="test",
                    path="data/test.bin",
                    sha256="b" * 64,
                    size_bytes=1,
                    state=ArtifactState.READY.value,
                    metadata_json={},
                )
            )
            session.add(
                ReviewDecision(
                    task_id=task["id"],
                    sample_id=sample.id,
                    scope_type="sample",
                    scope_id=sample.id,
                    category="ai_generated",
                    decision="approved_keep",
                    source="human",
                    context_json={},
                    supersedes_id=None,
                    is_active=True,
                )
            )
            sample_id = sample.id

        overview_statements: list[str] = []

        def capture_overview_statement(_, __, statement, ___, ____, _____) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                overview_statements.append(statement)

        event.listen(
            app.state.database.engine,
            "before_cursor_execute",
            capture_overview_statement,
        )
        try:
            overview = client.get(f"/api/tasks/{task['id']}/overview")
        finally:
            event.remove(
                app.state.database.engine,
                "before_cursor_execute",
                capture_overview_statement,
            )
        assert overview.status_code == 200
        overview_sql = "\n".join(overview_statements).lower()
        assert "latent_entries" not in overview_sql
        assert "from exports" not in overview_sql
        summary = overview.json()
        assert summary["samples_total"] == 1
        assert summary["samples_valid"] == 1
        assert "missing_caption" not in summary
        assert summary["leaf_clusters"] == 1
        assert summary["ready_artifacts"] == 1
        assert "latent_entries" not in summary
        assert "stages" not in summary
        assert "exports" not in summary
        assert summary["evidence_codes"] == [{"name": "watermark_probability", "count": 1}]
        assert summary["review_counts"][0]["decision"] == "approved_keep"

        clusters = client.get(f"/api/tasks/{task['id']}/clusters").json()
        assert clusters["total"] == 1
        assert clusters["items"][0]["representative_sample_id"] == sample_id
        assert clusters["items"][0]["representative_path"] == "artist/sample.png"

        risks = client.get(
            f"/api/tasks/{task['id']}/risks",
            params={"code": "watermark_probability"},
        ).json()
        assert risks["total"] == 1
        assert risks["items"][0]["bbox"] == [0.1, 0.2, 0.3, 0.4]
        assert risks["items"][0]["review_only"] is True

        thumbnail = client.get(
            f"/api/tasks/{task['id']}/samples/{sample_id}/thumbnail",
            params={"size": 128},
        )
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(thumbnail.content)) as rendered:
            assert rendered.width <= 128
            assert rendered.height <= 128
        assert _sha256(image_path) == source_digest
        assert list((project / "data" / "tasks" / task["id"] / "thumbnails").rglob("*.jpg"))

        directories = client.get(
            "/api/filesystem/directories",
            params={"path": str(source)},
        ).json()
        assert directories["current"] == str(source.resolve())
        assert {entry["name"] for entry in directories["entries"]} == {"artist"}


def test_folder_scoped_clusters_risks_and_reversible_export_exclusions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    image_specs = {
        "Alpha/deeper/a.png": "red",
        "beta/b.png": "green",
        "root.png": "blue",
    }
    for relative_path, color in image_specs.items():
        path = source.joinpath(*Path(relative_path).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (48, 48), color).save(path)
    original_digest = _sha256(source / "Alpha" / "deeper" / "a.png")
    project = tmp_path / "project"
    project.mkdir()
    app = create_app(
        database_path=tmp_path / "scoped.db",
        enforce_runtime=False,
        project_root=project,
    )

    with TestClient(app) as client:
        task = _create_task(
            app,
            name="folder scoped",
            source_root=source,
            output_root=tmp_path / "output",
        )
        other_task = _create_task(app, name="other", source_root=source)
        with app.state.database.write_session() as session:
            task_row = session.get(Task, task["id"])
            assert task_row is not None
            task_row.status = TaskStatus.EVIDENCE_REVIEW.value
            alpha = _sample_row(
                task_id=task["id"],
                source_root=source,
                relative_path="Alpha/deeper/a.png",
                artist_scope="Alpha",
                size=1024,
            )
            beta = _sample_row(
                task_id=task["id"],
                source_root=source,
                relative_path="beta/b.png",
                artist_scope="beta",
                size=1024,
            )
            root = _sample_row(
                task_id=task["id"],
                source_root=source,
                relative_path="root.png",
                artist_scope="__root__",
            )
            foreign = _sample_row(
                task_id=other_task["id"],
                source_root=source,
                relative_path="beta/b.png",
                artist_scope="beta",
            )
            session.add_all([alpha, beta, root, foreign])
            session.flush()

            artist_cluster = ClusterNode(
                task_id=task["id"],
                parent_id=None,
                cluster_key="Alpha:leaf:0",
                scope_kind="artist",
                scope_id="Alpha",
                level=0,
                label=None,
                size=1,
                centroid_artifact_id=None,
                metadata_json={
                    "is_leaf": True,
                    "representative_sample_id": alpha.id,
                },
            )
            global_cluster = ClusterNode(
                task_id=task["id"],
                parent_id=None,
                cluster_key="global:leaf:0",
                scope_kind="global",
                scope_id="__global__",
                level=0,
                label=None,
                size=2,
                centroid_artifact_id=None,
                metadata_json={
                    "is_leaf": True,
                    "representative_sample_id": beta.id,
                },
            )
            session.add_all([artist_cluster, global_cluster])
            session.flush()
            session.add_all(
                [
                    ClusterMembership(
                        cluster_id=artist_cluster.id,
                        sample_id=alpha.id,
                        task_id=task["id"],
                        score=0.99,
                        is_representative=True,
                    ),
                    ClusterMembership(
                        cluster_id=global_cluster.id,
                        sample_id=alpha.id,
                        task_id=task["id"],
                        score=0.91,
                        is_representative=False,
                    ),
                    ClusterMembership(
                        cluster_id=global_cluster.id,
                        sample_id=beta.id,
                        task_id=task["id"],
                        score=0.95,
                        is_representative=True,
                    ),
                ]
            )
            for sample, code, severity, value in (
                (alpha, "watermark_probability", "high", 0.9),
                (alpha, "ocr_text_area_ratio", "medium", 0.4),
                (beta, "watermark_probability", "low", 0.2),
                (root, "decode_warning", "fatal", 1.0),
            ):
                session.add(
                    Evidence(
                        task_id=task["id"],
                        sample_id=sample.id,
                        code=code,
                        source="test_detector",
                        value_json=value,
                        threshold_json=0.5,
                        value_number=value,
                        threshold_number=0.5,
                        metadata_json={"test": True},
                        severity=severity,
                        review_only=code != "decode_warning",
                        bbox_json=None,
                        algorithm_version="test",
                    )
                )
            alpha_id = alpha.id
            beta_id = beta.id
            root_id = root.id
            foreign_id = foreign.id
            global_cluster_id = global_cluster.id

        folders_response = client.get(f"/api/tasks/{task['id']}/folders")
        assert folders_response.status_code == 200
        folders = {item["folder_id"]: item for item in folders_response.json()["items"]}
        assert list(folders) == ["__root__", "Alpha", "beta"]
        assert folders["__root__"]["display_name"] == "根目录"
        assert folders["Alpha"] == {
            "folder_id": "Alpha",
            "display_name": "Alpha",
            "sample_count": 1,
            "leaf_cluster_count": 2,
            "risk_sample_count": 1,
            "risk_evidence_count": 2,
        }

        clusters_response = client.get(
            f"/api/tasks/{task['id']}/clusters",
            params={"folder": "Alpha"},
        )
        assert clusters_response.status_code == 200
        clusters = {item["cluster_id"]: item for item in clusters_response.json()["items"]}
        assert len(clusters) == 2
        assert clusters[global_cluster_id]["total_size"] == 2
        assert clusters[global_cluster_id]["folder_size"] == 1
        assert clusters[global_cluster_id]["representative_sample_id"] == alpha_id

        members = client.get(
            f"/api/tasks/{task['id']}/clusters/{global_cluster_id}/samples",
            params={"folder": "Alpha"},
        )
        assert members.status_code == 200
        assert members.json()["total"] == 1
        assert members.json()["items"][0]["sample_id"] == alpha_id
        assert members.json()["items"][0]["score"] == 0.91
        assert (
            client.get(
                f"/api/tasks/{other_task['id']}/clusters/{global_cluster_id}/samples"
            ).status_code
            == 404
        )

        risks_response = client.get(
            f"/api/tasks/{task['id']}/risk-samples",
            params={"folder": "Alpha"},
        )
        assert risks_response.status_code == 200
        risk_item = risks_response.json()["items"][0]
        assert risks_response.json()["total"] == 1
        assert risk_item["sample_id"] == alpha_id
        assert risk_item["highest_severity"] == "high"
        assert risk_item["evidence_count"] == 2
        assert risk_item["evidence_codes"] == [
            "ocr_text_area_ratio",
            "watermark_probability",
        ]
        filtered_risks = client.get(
            f"/api/tasks/{task['id']}/risk-samples",
            params={"folder": "Alpha", "code": "ocr_text_area_ratio"},
        ).json()
        assert filtered_risks["items"][0]["evidence_count"] == 1
        assert filtered_risks["items"][0]["highest_severity"] == "medium"
        severity_filtered_risks = client.get(
            f"/api/tasks/{task['id']}/risk-samples",
            params={"severity": "fatal"},
        ).json()
        assert severity_filtered_risks["total"] == 1
        assert severity_filtered_risks["items"][0]["sample_id"] == root_id
        risk_detail = client.get(f"/api/tasks/{task['id']}/risk-samples/{alpha_id}").json()
        assert len(risk_detail["evidence"]) == 2
        assert risk_detail["manually_excluded"] is False
        assert (
            client.get(
                f"/api/tasks/{task['id']}/risk-samples",
                params={"folder": "missing"},
            ).status_code
            == 404
        )

        assert (
            client.post(
                f"/api/tasks/{task['id']}/manual-exclusions",
                json={"sample_ids": [], "excluded": True},
            ).status_code
            == 422
        )
        exclude = client.post(
            f"/api/tasks/{task['id']}/manual-exclusions",
            json={
                "sample_ids": [alpha_id, alpha_id],
                "excluded": True,
                "context": {"page": "clusters", "folder_id": "Alpha"},
            },
        )
        assert exclude.status_code == 200
        assert exclude.json() == {"selected": 1, "changed": 1, "excluded": True}
        assert _sha256(source / "Alpha" / "deeper" / "a.png") == original_digest
        after_exclude = client.get(
            f"/api/tasks/{task['id']}/risk-samples",
            params={"folder": "Alpha"},
        ).json()
        assert after_exclude["items"][0]["manually_excluded"] is True
        overview = client.get(f"/api/tasks/{task['id']}/overview").json()
        assert "stages" not in overview
        assert overview["samples_total"] == 3
        task_view = TaskService(app.state.database).get_task(task["id"])
        with app.state.database.read_session() as session:
            workspace = DatasetWorkspaceRepository(project_root=project).load(
                session,
                task_view,
            )
        assert [sample.sample_id for sample in workspace.samples] == [beta_id, root_id]
        assert workspace.datasets[0].sample_ids == (beta_id,)

        undo = client.post(
            f"/api/tasks/{task['id']}/manual-exclusions",
            json={"sample_ids": [alpha_id], "excluded": False},
        )
        assert undo.status_code == 200
        assert undo.json() == {"selected": 1, "changed": 1, "excluded": False}
        with app.state.database.read_session() as session:
            history = session.scalars(
                select(ReviewDecision)
                .where(
                    ReviewDecision.task_id == task["id"],
                    ReviewDecision.sample_id == alpha_id,
                    ReviewDecision.category == "manual_exclude",
                )
                .order_by(ReviewDecision.created_at, ReviewDecision.id)
            ).all()
        assert len(history) == 2
        assert sum(item.is_active for item in history) == 1
        assert next(item for item in history if item.is_active).decision == "approved_keep"

        cross_task = client.post(
            f"/api/tasks/{task['id']}/manual-exclusions",
            json={"sample_ids": [beta_id, foreign_id], "excluded": True},
        )
        assert cross_task.status_code == 409
        with app.state.database.read_session() as session:
            beta_manual_count = session.scalar(
                select(func.count())
                .select_from(ReviewDecision)
                .where(
                    ReviewDecision.task_id == task["id"],
                    ReviewDecision.sample_id == beta_id,
                    ReviewDecision.category == "manual_exclude",
                )
            )
        assert beta_manual_count == 0

        reclassified = client.post(
            f"/api/tasks/{task['id']}/watermark-review-threshold",
            json={"threshold": 0.85},
        )
        assert reclassified.status_code == 200
        assert reclassified.json() == {
            "threshold": 0.85,
            "updated": 2,
            "candidates": 1,
        }
        watermark_candidates = client.get(
            f"/api/tasks/{task['id']}/risk-samples",
            params={"code": "watermark_probability", "severity": "medium"},
        ).json()
        assert watermark_candidates["total"] == 1
        assert watermark_candidates["items"][0]["sample_id"] == alpha_id
        watermark_detail = client.get(
            f"/api/tasks/{task['id']}/risk-samples/{alpha_id}",
            params={"code": "watermark_probability", "severity": "medium"},
        ).json()
        assert watermark_detail["evidence"][0]["threshold_number"] == 0.85
        assert watermark_detail["evidence"][0]["metadata"]["candidate"] is True

        with app.state.database.write_session() as session:
            task_row = session.get(Task, task["id"])
            assert task_row is not None
            task_row.status = TaskStatus.PAUSED.value
            task_row.resume_state = TaskStatus.EVIDENCE_REVIEW.value
        paused_exclude = client.post(
            f"/api/tasks/{task['id']}/manual-exclusions",
            json={"sample_ids": [root_id], "excluded": True},
        )
        assert paused_exclude.status_code == 200
        with app.state.database.write_session() as session:
            task_row = session.get(Task, task["id"])
            assert task_row is not None
            task_row.status = TaskStatus.COMPLETED.value
            task_row.resume_state = None
        locked = client.post(
            f"/api/tasks/{task['id']}/manual-exclusions",
            json={"sample_ids": [root_id], "excluded": False},
        )
        assert locked.status_code == 409
        oversized = client.post(
            f"/api/tasks/{task['id']}/manual-exclusions",
            json={"sample_ids": ["x"] * 5001, "excluded": True},
        )
        assert oversized.status_code == 422


def test_thumbnail_refuses_source_mutation_before_cache_creation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    image_path = source / "sample.png"
    Image.new("RGB", (32, 32), "green").save(image_path)
    digest = _sha256(image_path)
    app = create_app(
        database_path=tmp_path / "mutation.db",
        enforce_runtime=False,
        project_root=tmp_path / "project",
    )
    with TestClient(app) as client:
        task = _create_task(app, name="mutation", source_root=source)
        stat = image_path.stat()
        with app.state.database.write_session() as session:
            sample = Sample(
                task_id=task["id"],
                relative_path="sample.png",
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=digest,
                pixel_sha256=digest,
                media_kind="image",
                artist_scope="__root__",
                scan_state="valid",
                encoded_width=32,
                encoded_height=32,
                display_width=32,
                display_height=32,
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
            sample_id = sample.id
        image_path.write_bytes(image_path.read_bytes() + b"changed")

        response = client.get(f"/api/tasks/{task['id']}/samples/{sample_id}/thumbnail")
        assert response.status_code == 409
        assert "changed after scanning" in response.json()["detail"]
