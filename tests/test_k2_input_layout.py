from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.models import Artifact, PhaseCheckpoint, Sample
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from dataset_audit_studio.scanner.manifest import load_manifest, manifest_path
from dataset_audit_studio.scanner.service import DatasetScanner
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

PROFILES = ("artist_concept", "character_concept", "general")


def _save_image(path: Path, color: str = "#2b6cb0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)


def _profile_components(profile: str, *, recursive: bool = True) -> dict:
    components = materialize_profile(profile)["components"]
    scan = components["media.scan"]["config"]
    scan["recursive"] = recursive
    scan["resolutions"] = [64]
    scan["batch_size"] = 1
    scan["cpu_workers"] = 1
    return components


def _create_profile_task(client: TestClient, source: Path, profile: str, *, recursive: bool = True):
    return client.post(
        "/api/tasks",
        json={
            "name": f"layout-{profile}",
            "source_root": str(source),
            "profile": profile,
            "components": _profile_components(profile, recursive=recursive),
        },
    )


def _profile_task_config(profile: str, *, recursive: bool = True) -> dict:
    config = ComponentTaskConfigMaterializer().materialize(
        _profile_components(profile, recursive=recursive),
        profile=profile,
    )
    config["scan"]["recursive"] = recursive
    return config


@pytest.mark.parametrize("profile", PROFILES)
def test_builtin_profile_accepts_flat_media_at_task_creation(tmp_path: Path, profile: str) -> None:
    source = tmp_path / profile / "flat"
    source.mkdir(parents=True)
    _save_image(source / "image.png")

    app = create_app(database_path=tmp_path / f"{profile}-flat.db", enforce_runtime=False)
    with TestClient(app) as client:
        response = _create_profile_task(client, source, profile)

    assert response.status_code == 201


@pytest.mark.parametrize("profile", PROFILES)
def test_builtin_profile_flat_media_uses_root_scope(
    database, task_service: TaskService, tmp_path: Path, profile: str
) -> None:
    source = tmp_path / profile / "flat-scope"
    source.mkdir(parents=True)
    _save_image(source / "flat.png")
    project = tmp_path / f"{profile}-project"
    project.mkdir()

    task = task_service.create_task(
        name=f"flat scope {profile}",
        source_root=str(source),
        output_root=None,
        config=_profile_task_config(profile),
    )
    queued = task_service.queue_task(task.id)
    claimed = task_service.claim_next(owner=f"flat-{profile}", lease_seconds=120)
    assert claimed is not None
    summary = DatasetScanner(task_service, project_root=project).run_scanning(claimed.token)

    assert summary.discovered == 1
    with database.read_session() as session:
        scopes = dict(
            session.execute(
                select(Sample.relative_path, Sample.artist_scope).where(Sample.task_id == queued.id)
            ).all()
        )
    assert scopes["flat.png"].encode("ascii").hex() == "5f5f726f6f745f5f"
    assert scopes == {"flat.png": "__root__"}


def test_legacy_root_sentinel_manifest_still_loads(tmp_path: Path) -> None:
    source = tmp_path / "legacy-root-source"
    source.mkdir()
    image = source / "flat.png"
    _save_image(image)
    project = tmp_path / "project"
    project.mkdir()
    config_hash = "a" * 64
    manifest = manifest_path("legacy-root", config_hash, project_root=project)
    manifest.parent.mkdir(parents=True)
    records = (
        {
            "record_type": "header",
            "schema_version": 1,
            "source_root": str(source.resolve()),
            "config_hash": config_hash,
            "scan_config": {},
            "created_at": "2026-07-30T00:00:00+00:00",
            "item_count": 1,
            "ignored_reparse_count": 0,
            "ignored_directory_count": 0,
        },
        {
            "record_type": "media",
            "relative_path": "flat.png",
            "source_size": image.stat().st_size,
            "source_mtime_ns": image.stat().st_mtime_ns,
            "media_kind_hint": "image",
            "artist_scope": "__root__",
        },
    )
    manifest.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
    expected_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    info, discovery = load_manifest(
        manifest,
        source_root=source,
        expected_config_hash=config_hash,
        expected_sha256=expected_sha256,
        project_root=project,
    )

    assert info.item_count == 1
    assert discovery.items[0].artist_scope.encode("ascii").hex() == "5f5f726f6f745f5f"
    assert [(item.relative_path, item.artist_scope) for item in discovery.items] == [
        ("flat.png", "__root__")
    ]


@pytest.mark.parametrize("profile", PROFILES)
def test_builtin_profile_accepts_multiple_one_level_folders(tmp_path: Path, profile: str) -> None:
    source = tmp_path / profile / "one-level"
    source.mkdir(parents=True)
    _save_image(source / "1_xx" / "first.png")
    _save_image(source / "second concept" / "second.png", "#c53030")

    app = create_app(database_path=tmp_path / f"{profile}-one-level.db", enforce_runtime=False)
    with TestClient(app) as client:
        response = _create_profile_task(client, source, profile)

    assert response.status_code == 201


def test_one_level_folder_name_is_preserved_in_existing_scope(
    database, task_service: TaskService, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _save_image(source / "1_xx" / "first.png")
    _save_image(source / "second concept" / "second.png", "#c53030")
    project = tmp_path / "project"
    project.mkdir()

    task = task_service.create_task(
        name="opaque scopes",
        source_root=str(source),
        output_root=None,
        config=_profile_task_config("character_concept"),
    )
    queued = task_service.queue_task(task.id)
    claimed = task_service.claim_next(owner="layout-test", lease_seconds=120)
    assert claimed is not None
    summary = DatasetScanner(task_service, project_root=project).run_scanning(claimed.token)

    assert summary.discovered == 2
    with database.read_session() as session:
        scopes = dict(
            session.execute(
                select(Sample.relative_path, Sample.artist_scope).where(Sample.task_id == queued.id)
            ).all()
        )
    assert scopes == {
        "1_xx/first.png": "1_xx",
        "second concept/second.png": "second concept",
    }


@pytest.mark.parametrize("profile", PROFILES)
def test_mixed_root_and_one_level_media_is_rejected_before_task_creation(
    tmp_path: Path, profile: str
) -> None:
    source = tmp_path / profile / "mixed"
    source.mkdir(parents=True)
    _save_image(source / "root.png")
    _save_image(source / "A-folder" / "child.png")
    app = create_app(database_path=tmp_path / f"{profile}-mixed.db", enforce_runtime=False)

    with TestClient(app) as client:
        before = client.get("/api/tasks").json()["total"]
        response = _create_profile_task(client, source, profile)
        after = client.get("/api/tasks").json()["total"]

    assert response.status_code == 422
    assert response.json()["detail"].startswith("Source layout error [mixed_media]")
    assert "root example: root.png" in response.json()["detail"]
    assert "first-level example: A-folder/child.png" in response.json()["detail"]
    assert after == before


def test_resolution_concept_image_layout_is_rejected_with_resolution_hint(tmp_path: Path) -> None:
    source = tmp_path / "resolution-layer"
    source.mkdir()
    _save_image(source / "1216" / "concept" / "image.png")
    app = create_app(database_path=tmp_path / "resolution-layer.db", enforce_runtime=False)

    with TestClient(app) as client:
        response = _create_profile_task(client, source, "artist_concept", recursive=False)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail.startswith("Source layout error [nested_media]")
    assert "maximum depth 1" in detail
    assert "1216/concept/image.png" in detail
    assert "choose the specific resolution directory" in detail.lower()


def test_non_media_files_and_empty_directories_do_not_affect_layout(tmp_path: Path) -> None:
    source = tmp_path / "annotations"
    source.mkdir()
    _save_image(source / "image.png")
    (source / "notes.txt").write_text("caption", encoding="utf-8")
    (source / "metadata.json").write_text("{}", encoding="utf-8")
    (source / "image.latent").write_bytes(b"latent")
    (source / "empty" / "nested").mkdir(parents=True)
    (source / "1216" / "concept").mkdir(parents=True)
    (source / "1216" / "concept" / "notes.txt").write_text("not media", encoding="utf-8")
    app = create_app(database_path=tmp_path / "annotations.db", enforce_runtime=False)

    with TestClient(app) as client:
        response = _create_profile_task(client, source, "general")

    assert response.status_code == 201


def test_profile_layout_is_rechecked_before_first_manifest_and_scan_records(
    database, task_service: TaskService, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _save_image(source / "concept" / "initial.png")
    project = tmp_path / "project"
    project.mkdir()
    task = task_service.create_task(
        name="changed after creation",
        source_root=str(source),
        output_root=None,
        config=_profile_task_config("general", recursive=False),
    )
    _save_image(source / "concept" / "nested" / "late.png", "#c53030")
    queued = task_service.queue_task(task.id)
    claimed = task_service.claim_next(owner="layout-change", lease_seconds=120)
    assert claimed is not None

    with pytest.raises(ValueError, match=r"Source layout error \[nested_media\]"):
        DatasetScanner(task_service, project_root=project).run_scanning(claimed.token)

    output_manifest = manifest_path(task.id, task.config_hash, project_root=project)
    assert not output_manifest.exists()
    with database.read_session() as session:
        sample_count = session.scalar(
            select(func.count()).select_from(Sample).where(Sample.task_id == queued.id)
        )
        assert sample_count == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(PhaseCheckpoint)
                .where(PhaseCheckpoint.task_id == queued.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Artifact)
                .where(Artifact.task_id == queued.id, Artifact.kind == "scan_manifest")
            )
            == 0
        )
    task_service.request_terminate(queued.id, force=True)
