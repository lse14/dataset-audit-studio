from __future__ import annotations

from pathlib import Path

import pytest
from dataset_audit_studio.adapters import json_artifact_store as artifact_store_module
from dataset_audit_studio.adapters.json_artifact_store import JsonArtifactStore
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService


def _task_id(tasks: TaskService, tmp_path: Path) -> str:
    source = tmp_path / "source"
    source.mkdir()
    task = tasks.create_task(
        name="json artifact",
        source_root=str(source),
        output_root=str(tmp_path / "output"),
        config=ComponentTaskConfigMaterializer().materialize(
            materialize_profile("general")["components"],
            profile="general",
            require_profile=True,
        ),
    )
    return task.id


def test_json_artifact_is_atomic_registered_and_tamper_evident(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _task_id(task_service, tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    store = JsonArtifactStore(project_root=project)
    cache_key = "a" * 64
    artifact = store.write(
        task_id=task_id,
        producer_id="analysis.sae",
        kind="sae_artifact",
        cache_key=cache_key,
        payload={"schema": "analysis.sae.v1", "samples": ["sample-b", "sample-a"]},
    )
    assert store.load(artifact) == {
        "schema": "analysis.sae.v1",
        "samples": ["sample-b", "sample-a"],
    }
    assert not list(project.rglob("*.part"))

    with database.write_session() as session:
        store.register(
            session,
            task_id=task_id,
            phase="exporting",
            artifact=artifact,
            metadata={"capability": "analysis.sae.v1"},
        )
    with database.read_session() as session:
        assert store.registered(
            session,
            task_id=task_id,
            producer_id="analysis.sae",
            kind="sae_artifact",
            cache_key=cache_key,
        ) == artifact

    path = project.joinpath(*Path(artifact.relative_path).parts)
    path.write_text('{"schema":"analysis.sae.v1","samples":[]}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="SHA-256 changed"):
        store.load(artifact)
    with (
        database.read_session() as session,
        pytest.raises(RuntimeError, match="changed on disk"),
    ):
        store.registered(
            session,
            task_id=task_id,
            producer_id="analysis.sae",
            kind="sae_artifact",
            cache_key=cache_key,
        )


def test_json_artifact_replacement_invalidates_old_reference(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = JsonArtifactStore(project_root=project)
    task_id = "00000000-0000-0000-0000-000000000001"
    first = store.write(
        task_id=task_id,
        producer_id="latent.resolve",
        kind="latent_reference",
        cache_key="b" * 64,
        payload={"schema": "latent.reference.v1", "datasets": []},
    )
    second = store.write(
        task_id=task_id,
        producer_id="latent.resolve",
        kind="latent_reference",
        cache_key="b" * 64,
        payload={"schema": "latent.reference.v1", "datasets": [{"stage": 1}]},
    )
    assert first.relative_path == second.relative_path
    assert first.sha256 != second.sha256
    with pytest.raises(RuntimeError, match="SHA-256 changed"):
        store.load(first)
    assert store.load(second)["datasets"] == [{"stage": 1}]
    assert not list(project.rglob("*.part"))


def test_json_artifact_retries_windows_sharing_violation_during_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = JsonArtifactStore(project_root=project)
    original_fsync = artifact_store_module.os.fsync
    attempts = 0
    delays: list[float] = []

    def fsync_with_sharing_violation(fd) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = PermissionError(13, "sharing violation")
            error.winerror = 32
            raise error
        original_fsync(fd)

    monkeypatch.setattr(artifact_store_module.os, "fsync", fsync_with_sharing_violation)
    monkeypatch.setattr(artifact_store_module.time, "sleep", delays.append)

    store.write(
        task_id="00000000-0000-0000-0000-000000000001",
        producer_id="analysis.sae",
        kind="sae_artifact",
        cache_key="c" * 64,
        payload={"schema": "analysis.sae.v1"},
    )

    assert attempts == 3
    assert delays == [0.1, 0.2]


def test_json_artifact_cleanup_does_not_hide_publish_error(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = JsonArtifactStore(project_root=project)
    original_unlink = Path.unlink

    def fail_replace(_source, _destination) -> None:
        error = PermissionError(13, "access denied")
        error.winerror = 5
        raise error

    def fail_unlink(path, *args, **kwargs):
        if path.name.endswith(".part"):
            error = PermissionError(13, "sharing violation")
            error.winerror = 32
            raise error
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifact_store_module.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    monkeypatch.setattr(artifact_store_module.time, "sleep", lambda _delay: None)

    with pytest.raises(PermissionError, match="access denied") as raised:
        store.write(
            task_id="00000000-0000-0000-0000-000000000001",
            producer_id="analysis.sae",
            kind="sae_artifact",
            cache_key="d" * 64,
            payload={"schema": "analysis.sae.v1"},
        )

    assert raised.value.winerror == 5
    assert any(
        "Unable to remove temporary artifact file" in note
        for note in raised.value.__notes__
    )
