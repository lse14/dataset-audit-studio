from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database import models
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import Sample, Task
from dataset_audit_studio.export.service import DatasetExporter
from dataset_audit_studio.export_runs.service import ExportRunService
from dataset_audit_studio.jobs.errors import InvalidTaskTransition
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from fastapi.testclient import TestClient


def _review_task(database, task_service: TaskService, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    image = source / "sample.png"
    content = b"r1032-first-export"
    image.write_bytes(content)
    components = materialize_profile("general")["components"]
    config = ComponentTaskConfigMaterializer().materialize(
        components, profile="general", require_profile=True
    )
    task = task_service.create_task(
        name="r1032 review task",
        source_root=str(source),
        output_root=None,
        config=config,
    )
    stat = image.stat()
    with database.write_session() as session:
        session.add(
            Sample(
                id="r1032-sample",
                task_id=task.id,
                relative_path="sample.png",
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=hashlib.sha256(content).hexdigest(),
                pixel_sha256="p" * 64,
                media_kind="image",
                artist_scope="__root__",
                scan_state="valid",
                encoded_width=1024,
                encoded_height=1024,
                display_width=1024,
                display_height=1024,
                frame_count=1,
                is_animated=False,
                exif_orientation=1,
                extracted_frame_path=None,
                export_requires_render=False,
                phash=None,
                colorhash=None,
                scan_algorithm_version="test",
            )
        )
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.EVIDENCE_REVIEW.value
    return task_service.get_task(task.id)


def _settings(output: Path) -> dict[str, object]:
    return {
        "output_root": str(output),
        "minimum_resolution": 512,
        "minimum_folder_images": 1,
        "add_repeat_prefix": True,
        "sample_seen_mode": "off",
        "sample_seen_target": None,
        "aesthetic_minimum": None,
    }


def test_first_review_export_freezes_snapshot_and_completes_task_without_event(
    database, task_service, tmp_path
) -> None:
    task = _review_task(database, task_service, tmp_path)
    output = tmp_path / "first-export"
    output.mkdir()
    service = ExportRunService(database)
    preview = service.preview(task.id, **_settings(output))
    with database.read_session() as session:
        event_count = session.query(models.TaskEvent).filter_by(task_id=task.id).count()

    run = service.complete_first_copy_export(
        task.id,
        expected_version=task.row_version,
        preview_digest=preview.preview_digest,
        **_settings(output),
    )

    assert run.status == "queued"
    assert run.input_digest == preview.input_digest
    assert task_service.get_task(task.id).status == TaskStatus.COMPLETED.value
    with database.read_session() as session:
        row = session.get(models.ExportRun, run.id)
        assert row is not None
        assert row.input_snapshot_json["schema"] == "export.run.input.v2"
        assert row.input_snapshot_json["canonical_sample_ids"] == ["r1032-sample"]
        assert session.query(models.TaskEvent).filter_by(task_id=task.id).count() == event_count


def test_repeat_run_reuses_the_same_immutable_input_snapshot(
    database, task_service, tmp_path
) -> None:
    task = _review_task(database, task_service, tmp_path)
    first_output = tmp_path / "first-export"
    first_output.mkdir()
    service = ExportRunService(database)
    preview = service.preview(task.id, **_settings(first_output))
    first = service.complete_first_copy_export(
        task.id,
        expected_version=task.row_version,
        preview_digest=preview.preview_digest,
        **_settings(first_output),
    )
    repeat_output = tmp_path / "repeat-export"
    repeat_output.mkdir()
    repeat_preview = service.preview(task.id, **_settings(repeat_output))
    repeat = service.create(
        task.id,
        preview_digest=repeat_preview.preview_digest,
        **_settings(repeat_output),
    )

    with database.read_session() as session:
        first_row = session.get(models.ExportRun, first.id)
        repeat_row = session.get(models.ExportRun, repeat.id)
        assert first_row is not None and repeat_row is not None
        assert repeat_row.input_snapshot_json == first_row.input_snapshot_json
        assert repeat.input_digest == first.input_digest


def test_first_export_failure_does_not_change_completed_task_and_requires_new_output(
    database, task_service, tmp_path
) -> None:
    task = _review_task(database, task_service, tmp_path)
    output = tmp_path / "first-export"
    output.mkdir()
    service = ExportRunService(database)
    preview = service.preview(task.id, **_settings(output))
    service.complete_first_copy_export(
        task.id,
        expected_version=task.row_version,
        preview_digest=preview.preview_digest,
        **_settings(output),
    )
    (output / "external.txt").write_text("do not overwrite", encoding="utf-8")
    claimed = task_service.claim_next(owner="r1032-failing-worker", lease_seconds=60)
    assert claimed is not None

    from dataset_audit_studio.export_runs.executor import ExportRunExecutor

    failed = ExportRunExecutor(database).run(claimed.token)
    assert failed.status == "failed"
    assert task_service.get_task(task.id).status == TaskStatus.COMPLETED.value
    assert (output / "external.txt").read_text(encoding="utf-8") == "do not overwrite"

    retry_output = tmp_path / "retry-export"
    retry_output.mkdir()
    retry_preview = service.preview(task.id, **_settings(retry_output))
    retry = service.create(
        task.id,
        preview_digest=retry_preview.preview_digest,
        **_settings(retry_output),
    )
    assert retry.status == "queued"


def test_copy_review_gate_api_creates_first_run_without_task_export_phase(tmp_path) -> None:
    app = create_app(
        database_path=tmp_path / "api.db",
        project_root=tmp_path,
        enforce_runtime=False,
    )
    with TestClient(app) as client:
        task = _review_task(app.state.database, TaskService(app.state.database), tmp_path)
        output = tmp_path / "api-first-export"
        output.mkdir()
        settings = _settings(output)
        settings["image_format"] = "original"
        preview = client.post(
            f"/api/tasks/{task.id}/export-runs/preview",
            json=settings,
        )
        assert preview.status_code == 200
        release = client.post(
            f"/api/tasks/{task.id}/review-gate/release",
            json={
                "expected_gate": TaskStatus.EVIDENCE_REVIEW.value,
                "expected_version": task.row_version,
                **settings,
                "preview_digest": preview.json()["preview_digest"],
            },
        )
        assert release.status_code == 200
        assert release.json()["status"] == "queued"
        assert client.get(f"/api/tasks/{task.id}").json()["status"] == TaskStatus.COMPLETED.value


def test_task_service_rejects_copy_review_release_bypass(database, task_service, tmp_path) -> None:
    task = _review_task(database, task_service, tmp_path)
    with database.read_session() as session:
        before = session.query(models.TaskEvent).filter_by(task_id=task.id).count()

    with pytest.raises(InvalidTaskTransition, match="copy export must use ExportRunService"):
        task_service.release_review_gate(
            task.id,
            expected_gate=TaskStatus.EVIDENCE_REVIEW,
            expected_version=task.row_version,
        )

    current = task_service.get_task(task.id)
    assert current.status == TaskStatus.EVIDENCE_REVIEW.value
    assert current.resume_state is None
    with database.read_session() as session:
        after = session.query(models.TaskEvent).filter_by(task_id=task.id).count()
    assert after == before


def test_dataset_exporter_rejects_copy_worker_entry_before_legacy_stage_layout(
    database, task_service, tmp_path
) -> None:
    task = _review_task(database, task_service, tmp_path)
    output = tmp_path / "legacy-copy-output"
    output.mkdir()
    with database.write_session() as session:
        row = session.get(Task, task.id)
        assert row is not None
        row.output_root = str(output)
        row.status = TaskStatus.QUEUED.value
        row.resume_state = TaskStatus.EXPORTING.value
    claimed = task_service.claim_next(owner="legacy-copy-worker", lease_seconds=60)
    assert claimed is not None
    assert claimed.task.status == TaskStatus.EXPORTING.value

    with pytest.raises(InvalidTaskTransition, match="copy export must use ExportRunService"):
        DatasetExporter(task_service, project_root=tmp_path).run(claimed.token)

    assert not (output / "stage1").exists()


def test_copy_production_runtime_has_no_legacy_stage_or_latent_execution_imports() -> None:
    package = Path(__file__).parents[1] / "backend" / "dataset_audit_studio"
    sources = {
        relative: (package / relative).read_text(encoding="utf-8")
        for relative in (
            "export/service.py",
            "export/repository.py",
            "export_runs/planner.py",
            "app/component_execution.py",
            "app/modular_exporting_process.py",
            "workspace/service.py",
        )
    }

    assert "stage1" not in "\n".join(sources.values())
    assert "latent_resolver" not in sources["export_runs/planner.py"]
    assert "latent.resolve" not in sources["app/component_execution.py"]
    assert "latent.resolve" not in sources["app/modular_exporting_process.py"]
