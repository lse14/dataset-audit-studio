from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import json
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.components.artist_style.assets import (
    STYLE_MODEL_ID,
    STYLE_PREPROCESSING_VERSION,
)
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.components.artist_style.contracts import StyleSample, StyleScope
from dataset_audit_studio.components.dataset_export.contracts import PlannedFile
from dataset_audit_studio.database import models
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import (
    ComponentRun,
    Evidence,
    ReviewDecision,
    Sample,
    Task,
    WorkerLease,
)
from dataset_audit_studio.export.tree_publisher import ExportTreePublisher
from dataset_audit_studio.export_runs.errors import ExportRunError
from dataset_audit_studio.export_runs.planner import ExportRunPlanner
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.scoring.assets import EVIDENCE_SOURCES, PREPROCESSING_VERSIONS
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.style.repository import StyleRepository
from dataset_audit_studio.workspace.constants import (
    MANUAL_EXCLUSION_CATEGORY,
    MANUAL_EXCLUSION_DECISION,
)
from PIL import Image
from sqlalchemy import event, select


def test_export_run_model_and_single_target_worker_lease_contract() -> None:
    export_run = getattr(models, "ExportRun", None)

    assert export_run is not None
    assert export_run.__tablename__ == "export_runs"
    assert "output_key" in export_run.__table__.c
    assert export_run.__table__.c.output_key.unique is True
    assert models.WorkerLease.__table__.c.task_id.nullable is True
    assert "export_run_id" in models.WorkerLease.__table__.c
    constraints = {
        str(item.sqltext)
        for item in models.WorkerLease.__table__.constraints
        if hasattr(item, "sqltext")
    }
    assert any("task_id" in item and "export_run_id" in item for item in constraints)


def _completed_profile_task(
    database,
    task_service: TaskService,
    tmp_path: Path,
    *,
    resolutions: tuple[int, ...] = (512, 1024),
    image_bytes: bytes = b"export-run-source",
    caption: str | None = None,
):
    source = tmp_path / "source"
    source.mkdir()
    components = materialize_profile("general")["components"]
    components["media.scan"]["config"]["resolutions"] = list(resolutions)
    config = ComponentTaskConfigMaterializer().materialize(
        components, profile="general", require_profile=True
    )
    task = task_service.create_task(
        name="completed export source",
        source_root=str(source),
        output_root=str(tmp_path / "legacy-output"),
        config=config,
    )
    image = source / "sample.png"
    image.write_bytes(image_bytes)
    if caption is not None:
        image.with_suffix(".txt").write_text(caption, encoding="utf-8")
    stat = image.stat()
    with database.write_session() as session:
        session.add(
            Sample(
                id="export-run-sample",
                task_id=task.id,
                relative_path="sample.png",
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=hashlib.sha256(image_bytes).hexdigest(),
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
        row.status = TaskStatus.COMPLETED.value
    return task_service.get_task(task.id)


def _style_scope_hash(task, sample: Sample) -> str:
    source_path = Path(task.source_root, *Path(sample.relative_path).parts)
    scope = StyleScope(
        scope_id=sample.artist_scope,
        samples=(
            StyleSample(
                sample_id=sample.id,
                relative_path=sample.relative_path,
                artist_scope=sample.artist_scope,
                source_path=source_path,
                image_path=source_path,
                source_size=sample.source_size,
                source_mtime_ns=sample.source_mtime_ns,
                pixel_sha256=sample.pixel_sha256 or "",
            ),
        ),
    )
    return StyleRepository.scope_config_hash(scope, StyleConfig.from_task_config(task.config))


def _export_run_service():
    try:
        module = importlib.import_module("dataset_audit_studio.export_runs.service")
    except ModuleNotFoundError:
        return None
    service_type = getattr(module, "ExportRunService", None)
    if service_type is None:
        return None

    class PreviewingService:
        def __init__(self, database) -> None:
            self._service = service_type(database)

        def create(self, task_id: str, **kwargs):
            if kwargs.get("preview_digest") is None:
                preview = self._service.preview(
                    task_id,
                    output_root=kwargs["output_root"],
                    minimum_resolution=kwargs["minimum_resolution"],
                    aesthetic_minimum=kwargs.get("aesthetic_minimum"),
                    minimum_folder_images=kwargs.get("minimum_folder_images", 1),
                    add_repeat_prefix=kwargs.get("add_repeat_prefix", True),
                    sample_seen_mode=kwargs.get("sample_seen_mode", "off"),
                    sample_seen_target=kwargs.get("sample_seen_target"),
                    image_format=kwargs.get("image_format", "original"),
                )
                kwargs["preview_digest"] = preview.preview_digest
            return self._service.create(task_id, **kwargs)

        def __getattr__(self, name):
            return getattr(self._service, name)

    return PreviewingService


def _png_rgba_bytes() -> bytes:
    buffer = BytesIO()
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((1, 0), (10, 20, 30, 64))
    image.save(buffer, format="PNG")
    image.close()
    return buffer.getvalue()


def _track_encode_inside_write_session(monkeypatch):
    from dataset_audit_studio.database.session import Database
    from dataset_audit_studio.export import image_conversion, tree_publisher

    original_encode = image_conversion.encode_export_image
    original_write = Database.write_session
    in_write = {"value": False}
    encode_in_write: list[bool] = []

    @contextmanager
    def tracking_write(self):
        in_write["value"] = True
        try:
            with original_write(self) as session:
                yield session
        finally:
            in_write["value"] = False

    def tracking_encode(source, image_format):
        encode_in_write.append(in_write["value"])
        return original_encode(source, image_format)

    monkeypatch.setattr(Database, "write_session", tracking_write)
    monkeypatch.setattr(image_conversion, "encode_export_image", tracking_encode)
    monkeypatch.setattr(tree_publisher, "encode_export_image", tracking_encode)
    cache_mod = importlib.import_module("dataset_audit_studio.export_runs.transcode_cache")
    monkeypatch.setattr(cache_mod, "encode_export_image", tracking_encode)
    return encode_in_write


def test_completed_profile_task_creates_one_immutable_copy_run(
    database, task_service, tmp_path
) -> None:
    service_type = _export_run_service()
    assert service_type is not None
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "new-export"
    output.mkdir()
    service = service_type(database)
    with database.read_session() as session:
        event_count = session.query(models.TaskEvent).filter_by(task_id=task.id).count()

    run = service.create(task.id, output_root=str(output), minimum_resolution=512)

    assert run.task_id == task.id
    assert run.status == "queued"
    assert run.resolutions == (512,)
    assert run.aesthetic_minimum is None
    assert task_service.get_task(task.id).status == TaskStatus.COMPLETED.value
    with database.read_session() as session:
        assert session.query(models.TaskEvent).filter_by(task_id=task.id).count() == event_count


def test_r12_preview_normalizes_all_optional_eligibility_settings_and_uses_v3_snapshot(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r12-v3-settings"
    output.mkdir()
    service = _export_run_service()(database)
    identity = _aesthetic_identity(task)
    _add_aesthetic_evidence(database, task, "export-run-sample", 4.0, identity)
    with database.read_session() as session:
        sample = session.get(Sample, "export-run-sample")
        assert sample is not None
        style_scope_hash = _style_scope_hash(task, sample)
    with database.write_session() as session:
        session.add_all(
            (
                Evidence(
                    task_id=task.id,
                    sample_id="export-run-sample",
                    code="in_domain_probability",
                    source=identity["source"],
                    value_json=0.9,
                    threshold_json=None,
                    value_number=0.9,
                    threshold_number=None,
                    metadata_json={
                        "model_id": identity["model_id"],
                        "config_hash": identity["config_hash"],
                    },
                    severity="info",
                    review_only=False,
                    bbox_json=None,
                    algorithm_version=identity["algorithm_version"],
                ),
                Evidence(
                    task_id=task.id,
                    sample_id="export-run-sample",
                    code="artist_style_score",
                    source="artist_style_v1",
                    value_json=80.0,
                    threshold_json=StyleConfig.from_task_config(task.config).minimum_style_score,
                    value_number=80.0,
                    threshold_number=StyleConfig.from_task_config(
                        task.config
                    ).minimum_style_score,
                    metadata_json={
                        "config_hash": style_scope_hash,
                        "scope_id": "__root__",
                        "scope_size": 1,
                        "model_id": STYLE_MODEL_ID,
                        "strong_outlier": False,
                        "review_required": False,
                    },
                    severity="info",
                    review_only=True,
                    bbox_json=None,
                    algorithm_version=STYLE_PREPROCESSING_VERSION,
                ),
            )
        )
    _complete_r12_technical_metrics(database, task)

    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        domain_minimum=0.5,
        exclude_exact_visual_duplicates=True,
        style_outlier_mode="strong",
        aesthetic_minimum=3.0,
    )
    run = service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        domain_minimum=0.5,
        exclude_exact_visual_duplicates=True,
        style_outlier_mode="strong",
        aesthetic_minimum=3.0,
        preview_digest=preview.preview_digest,
    )

    assert preview.settings["schema"] == "export.run.settings.v3"
    assert preview.settings["domain_minimum"] == 0.5
    assert preview.settings["exclude_exact_visual_duplicates"] is True
    assert preview.settings["style_outlier_mode"] == "strong"
    assert preview.settings["aesthetic_minimum"] == 3.0
    assert run.settings["domain_minimum"] == 0.5
    assert run.settings["exclude_exact_visual_duplicates"] is True
    assert run.settings["style_outlier_mode"] == "strong"
    with database.read_session() as session:
        persisted = session.get(models.ExportRun, run.id)
        assert persisted is not None
        snapshot = persisted.input_snapshot_json
    assert str(Path(task.source_root).resolve()) not in json.dumps(snapshot)
    assert all("source_path" not in item for item in snapshot["files"])
    assert any(
        item.get("source_ref", {}).get("kind") == "task_source"
        for item in snapshot["files"]
    )
    planned = _export_run_planner()(database).build(run.id)
    assert planned.summary["schema"] == "export.run.summary.v3"
    assert planned.input_snapshot["schema"] == "export.run.input.v2"
    assert planned.summary["eligibility_digest"]
    assert any(
        item.source_path == Path(task.source_root, "sample.png") for item in planned.plan.files
    )


def test_r12_style_filter_fails_closed_on_scope_config_provenance_mismatch(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r12-style-provenance-mismatch"
    output.mkdir()
    with database.write_session() as session:
        session.add(
            Evidence(
                task_id=task.id,
                sample_id="export-run-sample",
                code="artist_style_score",
                source="artist_style_v1",
                value_json=80.0,
                threshold_json=StyleConfig.from_task_config(task.config).minimum_style_score,
                value_number=80.0,
                threshold_number=StyleConfig.from_task_config(
                    task.config
                ).minimum_style_score,
                metadata_json={
                    "config_hash": "c" * 64,
                    "scope_id": "__root__",
                    "scope_size": 1,
                    "model_id": STYLE_MODEL_ID,
                    "strong_outlier": False,
                    "review_required": False,
                },
                severity="info",
                review_only=True,
                bbox_json=None,
                algorithm_version=STYLE_PREPROCESSING_VERSION,
            )
        )

    preview = _export_run_service()(database).preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        style_outlier_mode="strong",
    )

    assert preview.included_count == 0
    assert preview.exclusion_counts["style_provenance_mismatch"] == 1


def test_r12_approved_keep_overrides_enabled_aesthetic_soft_filter(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    identity = _aesthetic_identity(task)
    _add_aesthetic_evidence(database, task, "export-run-sample", 1.0, identity)
    with database.write_session() as session:
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id="export-run-sample",
                scope_type="sample",
                scope_id="export-run-sample",
                category="aesthetic",
                decision="approved_keep",
                source="human",
                context_json={},
                supersedes_id=None,
                is_active=True,
            )
        )
    output = tmp_path / "r12-keep-overrides-aesthetic"
    output.mkdir()

    preview = _export_run_service()(database).preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        aesthetic_minimum=3.0,
    )

    assert preview.included_count == 1
    assert preview.exclusion_counts["aesthetic_below_minimum"] == 0


def test_r12_duplicate_evidence_producer_writes_idempotent_exact_and_visual_groups(
    database, task_service, tmp_path
) -> None:
    from dataset_audit_studio.app.duplicate_evidence import DuplicateEvidenceProducer

    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    duplicate = source / "duplicate.png"
    duplicate.write_bytes((source / "sample.png").read_bytes())
    stat = duplicate.stat()
    with database.write_session() as session:
        session.add(
            Sample(
                id="r12-duplicate",
                task_id=task.id,
                relative_path="duplicate.png",
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=hashlib.sha256(duplicate.read_bytes()).hexdigest(),
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
                phash="0" * 16,
                colorhash="0" * 16,
                scan_algorithm_version="test",
            )
        )
        original = session.get(Sample, "export-run-sample")
        assert original is not None
        original.phash = "0" * 16
        original.colorhash = "0" * 16

    producer = DuplicateEvidenceProducer(database)
    producer.produce(task.id, task.config_hash)
    producer.produce(task.id, task.config_hash)

    with database.read_session() as session:
        rows = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task.id,
                Evidence.code.in_(("duplicate_exact", "duplicate_visual")),
            )
            .order_by(Evidence.code, Evidence.sample_id)
        ).all()
    assert len(rows) == 4
    assert {row.metadata_json["group_key"] for row in rows if row.code == "duplicate_exact"}
    assert {row.metadata_json["group_key"] for row in rows if row.code == "duplicate_visual"}
    assert all(row.metadata_json["config_hash"] == task.config_hash for row in rows)


def _complete_r12_technical_metrics(database, task) -> None:
    with database.write_session() as session:
        session.add(
            ComponentRun(
                task_id=task.id,
                component_id="metrics.technical",
                component_version="1.0.0",
                phase="cpu_metrics",
                phase_order=20,
                execution="cpu_process",
                status="completed",
                config_hash=task.config_hash,
                config_digest="d" * 64,
                normalized_config_json={},
                dependency_ids_json=[],
                model_ids_json=[],
                checkpoint_json={"component_complete": True},
                completed_items=1,
                total_items=1,
                auto_enabled=False,
            )
        )


def _add_r12_duplicate_sample(
    database, task, source: Path, *, sample_id: str, relative_path: str, content: bytes, size: int
) -> None:
    path = source / relative_path
    path.write_bytes(content)
    stat = path.stat()
    with database.write_session() as session:
        session.add(
            Sample(
                id=sample_id,
                task_id=task.id,
                relative_path=relative_path,
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=hashlib.sha256(content).hexdigest(),
                pixel_sha256="p" * 64,
                media_kind="image",
                artist_scope="__root__",
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
                phash="0" * 16,
                colorhash="0" * 16,
                scan_algorithm_version="test",
            )
        )


def test_r12_duplicate_filter_unions_evidence_and_selects_largest_representative(
    database, task_service, tmp_path
) -> None:
    from dataset_audit_studio.app.duplicate_evidence import DuplicateEvidenceProducer

    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    original = source.joinpath("sample.png").read_bytes()
    _add_r12_duplicate_sample(
        database,
        task,
        source,
        sample_id="r12-exact",
        relative_path="exact.png",
        content=original,
        size=1536,
    )
    _add_r12_duplicate_sample(
        database,
        task,
        source,
        sample_id="r12-visual",
        relative_path="visual.png",
        content=b"different-source-bytes",
        size=2048,
    )
    with database.write_session() as session:
        base = session.get(Sample, "export-run-sample")
        assert base is not None
        base.phash = "0" * 16
        base.colorhash = "0" * 16
    _complete_r12_technical_metrics(database, task)
    DuplicateEvidenceProducer(database).produce(task.id, task.config_hash)
    output = tmp_path / "r12-duplicate-union"
    output.mkdir()

    preview = _export_run_service()(database).preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        exclude_exact_visual_duplicates=True,
    )

    assert preview.included_count == 1
    assert preview.exclusion_counts["duplicate_non_representative"] == 2
    assert preview.duplicate_groups == (
        {
            "group_keys": sorted(
                item
                for item in preview.duplicate_groups[0]["group_keys"]
                if item.startswith(("duplicate_exact:", "duplicate_visual:"))
            ),
            "member_count": 3,
            "manual_keep_count": 0,
            "representative_sample_id": "r12-visual",
        },
    )


def test_r12_duplicate_filter_rejects_malformed_group_evidence(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    _complete_r12_technical_metrics(database, task)
    with database.write_session() as session:
        session.add(
            Evidence(
                task_id=task.id,
                sample_id="export-run-sample",
                code="duplicate_exact",
                source="duplicate_evidence.v1",
                value_json="not-a-group",
                threshold_json=None,
                value_number=1.0,
                threshold_number=None,
                metadata_json={
                    "group_key": "",
                    "group_size": 1,
                    "config_hash": task.config_hash,
                    "provenance": {
                        "component_id": "metrics.technical",
                        "algorithm_version": "duplicate_evidence.v1",
                    },
                },
                severity="info",
                review_only=True,
                bbox_json=None,
                algorithm_version="duplicate_evidence.v1",
            )
        )
    output = tmp_path / "r12-malformed-duplicate"
    output.mkdir()

    with pytest.raises(ExportRunError) as error:
        _export_run_service()(database).preview(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
            exclude_exact_visual_duplicates=True,
        )
    assert error.value.code == "export_duplicate_evidence_invalid"


def test_r12_duplicate_evidence_provenance_changes_preview_digest(
    database, task_service, tmp_path
) -> None:
    from dataset_audit_studio.app.duplicate_evidence import DuplicateEvidenceProducer

    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    _add_r12_duplicate_sample(
        database,
        task,
        source,
        sample_id="r12-digest-duplicate",
        relative_path="digest-duplicate.png",
        content=(source / "sample.png").read_bytes(),
        size=1024,
    )
    with database.write_session() as session:
        base = session.get(Sample, "export-run-sample")
        assert base is not None
        base.phash = "0" * 16
        base.colorhash = "0" * 16
    _complete_r12_technical_metrics(database, task)
    DuplicateEvidenceProducer(database).produce(task.id, task.config_hash)
    output = tmp_path / "r12-duplicate-provenance-digest"
    output.mkdir()
    service = _export_run_service()(database)
    first = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        exclude_exact_visual_duplicates=True,
    )
    with database.write_session() as session:
        row = session.scalar(
            select(Evidence)
            .where(Evidence.task_id == task.id, Evidence.code == "duplicate_exact")
            .order_by(Evidence.id)
        )
        assert row is not None
        metadata = dict(row.metadata_json)
        metadata["provenance_note"] = "changed"
        row.metadata_json = metadata
    second = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        exclude_exact_visual_duplicates=True,
    )

    assert first.eligibility_digest != second.eligibility_digest
    assert first.preview_digest != second.preview_digest


def test_r12_duplicate_filter_fails_closed_without_technical_completion(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r12-duplicate-proof"
    output.mkdir()

    with pytest.raises(ExportRunError) as error:
        _export_run_service()(database).preview(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
            exclude_exact_visual_duplicates=True,
        )

    assert error.value.code == "export_duplicate_analysis_incomplete"


def test_r12_v2_export_run_settings_are_unsupported(database, task_service, tmp_path) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r12-v2-unsupported"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
    )
    with database.write_session() as session:
        row = session.get(models.ExportRun, run.id)
        assert row is not None
        settings = dict(row.settings_json)
        settings["schema"] = "export.run.settings.v2"
        row.settings_json = settings

    with pytest.raises(ExportRunError) as error:
        _export_run_planner()(database).build(run.id)

    assert error.value.code == "export_legacy_payload_unsupported"


def test_r12_preview_allows_empty_but_create_rejects_empty_output(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    identity = _aesthetic_identity(task)
    _add_aesthetic_evidence(database, task, "export-run-sample", 1.0, identity)
    output = tmp_path / "r12-empty-preview"
    output.mkdir()
    service = _export_run_service()(database)

    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        aesthetic_minimum=3.0,
    )
    assert preview.included_count == 0

    with pytest.raises(ExportRunError) as error:
        service.create(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
            aesthetic_minimum=3.0,
            preview_digest=preview.preview_digest,
        )
    assert error.value.code == "export_empty_output"


def test_r12_eligibility_digest_changes_with_active_decision(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r12-decision-digest"
    output.mkdir()
    service = _export_run_service()(database)
    first = service.preview(task.id, output_root=str(output), minimum_resolution=512)
    with database.write_session() as session:
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id="export-run-sample",
                scope_type="sample",
                scope_id="export-run-sample",
                category="manual",
                decision="approved_keep",
                source="human",
                context_json={},
                supersedes_id=None,
                is_active=True,
            )
        )
    second = service.preview(task.id, output_root=str(output), minimum_resolution=512)

    assert first.eligibility_digest != second.eligibility_digest
    assert first.preview_digest != second.preview_digest


def test_r1031_preview_and_create_freeze_single_dataset_contract(
    database, task_service, tmp_path
) -> None:
    service_type = _export_run_service()
    assert service_type is not None
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r1031-export"
    output.mkdir()
    service = service_type(database)

    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        aesthetic_minimum=None,
        minimum_folder_images=1,
        add_repeat_prefix=True,
        sample_seen_mode="off",
        sample_seen_target=None,
    )

    assert preview.preview_digest
    assert preview.included_count == 1
    assert preview.exclusion_counts["folder_below_minimum"] == 0
    assert preview.folders[0]["source_identifier"] == "source"
    assert preview.folders[0]["output_folder"] == "1_source"
    assert preview.folders[0]["exclusion_reason"] is None

    run = service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        aesthetic_minimum=None,
        minimum_folder_images=1,
        add_repeat_prefix=True,
        sample_seen_mode="off",
        sample_seen_target=None,
        preview_digest=preview.preview_digest,
    )

    assert run.resolutions == (512,)
    assert run.minimum_folder_images == 1
    assert run.add_repeat_prefix is True
    assert run.sample_seen_mode == "off"
    assert run.sample_seen_target is None


def test_export_run_freezes_image_format_and_defaults_to_original(
    database, task_service, tmp_path
) -> None:
    buffer = BytesIO()
    image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    image.save(buffer, format="PNG")
    image.close()
    task = _completed_profile_task(database, task_service, tmp_path, image_bytes=buffer.getvalue())
    output = tmp_path / "image-format-settings"
    output.mkdir()
    service = _export_run_service()(database)

    default_preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="original",
    )
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
    )
    run = service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
        preview_digest=preview.preview_digest,
    )

    assert default_preview.settings["image_format"] == "original"
    assert preview.settings["image_format"] == "webp"
    assert run.settings["image_format"] == "webp"


def test_export_run_plan_restores_selected_image_format_from_snapshot(
    database, task_service, tmp_path
) -> None:
    buffer = BytesIO()
    image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    image.save(buffer, format="PNG")
    image.close()
    task = _completed_profile_task(database, task_service, tmp_path, image_bytes=buffer.getvalue())
    output = tmp_path / "image-format-snapshot"
    output.mkdir()
    service = _export_run_service()(database)
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
    )
    run = service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
        preview_digest=preview.preview_digest,
    )

    plan = ExportRunPlanner(database).build(run.id).plan
    image = next(file for file in plan.files if file.kind == "converted_image")

    assert image.transcode_format == "webp"


def test_export_run_legacy_snapshot_without_image_format_defaults_to_original(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "legacy-image-format"
    output.mkdir()
    service = _export_run_service()(database)
    run = service.create(task.id, output_root=str(output), minimum_resolution=512)

    with database.write_session() as session:
        persisted = session.get(models.ExportRun, run.id)
        assert persisted is not None
        settings = copy.deepcopy(persisted.settings_json)
        settings.pop("image_format", None)
        persisted.settings_json = settings
        snapshot = copy.deepcopy(persisted.input_snapshot_json)
        snapshot["settings"].pop("image_format", None)
        snapshot["summary"]["settings"].pop("image_format", None)
        for entry in snapshot["files"]:
            entry.pop("transcode_format", None)
        persisted.input_snapshot_json = snapshot

    plan = ExportRunPlanner(database).build(run.id).plan
    image = next(file for file in plan.files if file.kind == "source_image")

    assert image.destination_relative == "1_source/sample.png"
    assert image.transcode_format is None


def test_export_run_rejects_an_unknown_image_format(database, task_service, tmp_path) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "invalid-image-format"
    output.mkdir()
    service = _export_run_service()(database)

    with pytest.raises(ExportRunError) as error:
        service.preview(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
            image_format="tiff",
        )

    assert error.value.code == "export_image_format_invalid"


@pytest.mark.parametrize(
    ("image_format", "suffix"),
    (("jpeg", ".jpg"), ("png", ".png"), ("webp", ".webp")),
)
def test_export_run_converts_images_and_keeps_paired_caption(
    database,
    task_service,
    tmp_path,
    image_format: str,
    suffix: str,
) -> None:
    source_image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    source_image.putpixel((1, 0), (10, 20, 30, 64))
    buffer = BytesIO()
    source_image.save(buffer, format="PNG")
    source_image.close()
    source_bytes = buffer.getvalue()
    task = _completed_profile_task(
        database,
        task_service,
        tmp_path,
        image_bytes=source_bytes,
        caption="training caption",
    )
    output = tmp_path / f"converted-{image_format}"
    output.mkdir()
    service = _export_run_service()(database)
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format=image_format,
    )
    service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format=image_format,
        preview_digest=preview.preview_digest,
    )
    claimed = task_service.claim_next(owner=f"converted-{image_format}", lease_seconds=60)
    assert claimed is not None
    completed = _export_run_executor()(database).run(claimed.token)

    exported = output / "1_source" / f"sample{suffix}"
    assert completed.status == "completed", completed.error_message
    assert exported.is_file()
    assert (output / "1_source" / "sample.txt").read_text(encoding="utf-8") == "training caption"
    assert (Path(task.source_root) / "sample.png").read_bytes() == source_bytes
    with Image.open(exported) as image:
        if image_format == "jpeg":
            assert image.mode == "RGB"
            assert min(image.getpixel((0, 0))) >= 245
        else:
            assert image.mode == "RGBA"
            assert image.getpixel((1, 0))[3] == 64
    manifest = json.loads((output / "export-run-manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["path"] == f"1_source/sample{suffix}")
    assert entry["size"] == exported.stat().st_size
    assert entry["sha256"] == hashlib.sha256(exported.read_bytes()).hexdigest()


def test_export_run_converted_same_stem_images_keep_distinct_paths_and_annotations(
    database, task_service, tmp_path
) -> None:
    source_image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    source_image.putpixel((1, 0), (10, 20, 30, 64))
    buffer = BytesIO()
    source_image.save(buffer, format="PNG")
    source_image.close()
    task = _completed_profile_task(
        database,
        task_service,
        tmp_path,
        image_bytes=buffer.getvalue(),
        caption="shared caption",
    )
    source = Path(task.source_root)
    second = Image.new("RGB", (2, 1), (220, 30, 40))
    second.save(source / "sample.jpg", format="JPEG", quality=95)
    second.close()
    second_path = source / "sample.jpg"
    second_stat = second_path.stat()
    with database.write_session() as session:
        session.add(
            Sample(
                id="export-run-sample-jpg",
                task_id=task.id,
                relative_path="sample.jpg",
                source_size=second_stat.st_size,
                source_mtime_ns=second_stat.st_mtime_ns,
                source_sha256=hashlib.sha256(second_path.read_bytes()).hexdigest(),
                pixel_sha256="q" * 64,
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
    output = tmp_path / "same-stem-converted"
    output.mkdir()
    service = _export_run_service()(database)
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="jpeg",
    )
    service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="jpeg",
        preview_digest=preview.preview_digest,
    )
    claimed = task_service.claim_next(owner="same-stem-converted-worker", lease_seconds=60)
    assert claimed is not None
    completed = _export_run_executor()(database).run(claimed.token)

    assert completed.status == "completed", completed.error_message
    assert (output / "1_source" / "sample.jpg").is_file()
    assert (output / "1_source" / "sample.png.jpg").is_file()
    assert (output / "1_source" / "sample.txt").read_text(encoding="utf-8") == "shared caption"
    assert (
        output / "1_source" / "sample.png.txt"
    ).read_text(encoding="utf-8") == "shared caption"


def test_r1031_executor_publishes_flat_training_folder_and_contract_manifest(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r1031-published"
    output.mkdir()
    service = _export_run_service()(database)
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        minimum_folder_images=1,
        add_repeat_prefix=True,
        sample_seen_mode="off",
        sample_seen_target=None,
    )
    service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        minimum_folder_images=1,
        add_repeat_prefix=True,
        sample_seen_mode="off",
        sample_seen_target=None,
        preview_digest=preview.preview_digest,
    )
    claimed = task_service.claim_next(owner="r1031-executor", lease_seconds=60)
    assert claimed is not None
    completed = _export_run_executor()(database).run(claimed.token)

    assert completed.status == "completed"
    assert (output / "1_source" / "sample.png").read_bytes() == b"export-run-source"
    assert not (output / "stage1").exists()
    manifest = json.loads((output / "export-run-manifest.json").read_text())
    assert manifest["schema"] == "export.run.v3"
    assert "resolutions" not in manifest
    assert all(str(Path(task.source_root)) not in json.dumps(manifest) for _ in [0])


def test_r1031_selection_validation_rejects_inconsistent_sample_seen_settings(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r1031-invalid-settings"
    output.mkdir()
    service = _export_run_service()(database)

    with pytest.raises(ExportRunError) as error:
        service.preview(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
            minimum_folder_images=1,
            add_repeat_prefix=False,
            sample_seen_mode="auto",
            sample_seen_target=None,
        )

    assert error.value.code == "export_repeat_prefix_required"


@pytest.mark.parametrize(
    ("kwargs", "code"),
    (
        ({"minimum_folder_images": 0}, "export_minimum_folder_images_invalid"),
        ({"sample_seen_mode": "invalid"}, "export_sample_seen_mode_invalid"),
        (
            {"sample_seen_mode": "manual", "sample_seen_target": None},
            "export_sample_seen_target_invalid",
        ),
        ({"sample_seen_mode": "off", "sample_seen_target": 1}, "export_sample_seen_target_invalid"),
        ({"add_repeat_prefix": "yes"}, "export_add_repeat_prefix_invalid"),
    ),
)
def test_r1031_selection_validation_rejects_invalid_settings(
    kwargs: dict[str, object], code: str, database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "invalid-selection"
    output.mkdir()

    with pytest.raises(ExportRunError) as error:
        _export_run_service()(database).preview(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
            **kwargs,
        )

    assert error.value.code == code


def test_r1031_area_qualification_uses_product_boundary(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "area-boundary-export"
    output.mkdir()
    service = _export_run_service()(database)

    with database.write_session() as session:
        row = session.get(Sample, "export-run-sample")
        assert row is not None
        row.display_width = 512
        row.display_height = 512
    boundary = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
    )
    assert boundary.included_count == 1

    with database.write_session() as session:
        row = session.get(Sample, "export-run-sample")
        assert row is not None
        row.display_height = 511
    below = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
    )
    assert below.included_count == 0
    assert below.exclusion_counts["resolution_below_minimum"] == 1


def test_r1031_nested_folder_preserves_deeper_relative_path(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    _add_sample_at(database, task, source, "nested-sample", "artist/sub/image.png")
    output = tmp_path / "nested-export"
    output.mkdir()
    preview = _export_run_service()(database).preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
    )

    assert preview.folders[0]["source_identifier"] == "artist"
    assert preview.folders[0]["output_folder"] == "1_artist"
    run = _export_run_service()(database).create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        preview_digest=preview.preview_digest,
    )
    destinations = {
        file.destination_relative
        for file in _export_run_planner()(database).build(run.id).plan.files
    }
    assert "1_artist/sub/image.png" in destinations


def test_r1031_preview_stale_after_canonical_cohort_changes(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "r1031-stale"
    output.mkdir()
    service = _export_run_service()(database)
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        minimum_folder_images=1,
        add_repeat_prefix=True,
        sample_seen_mode="off",
        sample_seen_target=None,
    )
    source = Path(task.source_root)
    _add_broad_sample(database, task, source, 1)

    with pytest.raises(ExportRunError) as error:
        service.create(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
            minimum_folder_images=1,
            add_repeat_prefix=True,
            sample_seen_mode="off",
            sample_seen_target=None,
            preview_digest=preview.preview_digest,
        )

    assert error.value.code == "export_preview_stale"


def test_export_run_rejects_an_output_root_inside_the_source_before_queueing(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = Path(task.source_root) / "nested-export"
    output.mkdir()

    with pytest.raises(ExportRunError) as error:
        _export_run_service()(database).create(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
        )

    assert error.value.code == "export_output_path_invalid"


def test_copy_export_run_without_aesthetic_minimum_does_not_query_evidence(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "unscored-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
    )
    statements: list[str] = []

    def capture_evidence_query(_, __, statement, ___, ____, _____) -> None:
        if "from evidence" in statement.casefold():
            statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", capture_evidence_query)
    try:
        _export_run_planner()(database).build(run.id)
    finally:
        event.remove(database.engine, "before_cursor_execute", capture_evidence_query)

    assert statements == []


def _export_run_planner():
    try:
        module = importlib.import_module("dataset_audit_studio.export_runs.planner")
    except ModuleNotFoundError:
        return None
    return getattr(module, "ExportRunPlanner", None)


def _add_broad_sample(database, task, source: Path, index: int) -> str:
    sample_id = f"export-run-sample-{index}"
    relative = f"samples/{index}.png"
    image = source / relative
    image.parent.mkdir(exist_ok=True)
    content = f"source-{index}".encode("ascii")
    image.write_bytes(content)
    stat = image.stat()
    with database.write_session() as session:
        session.add(
            Sample(
                id=sample_id,
                task_id=task.id,
                relative_path=relative,
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=hashlib.sha256(content).hexdigest(),
                pixel_sha256=f"{index:064x}",
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
    return sample_id


def _add_sample_at(database, task, source: Path, sample_id: str, relative: str) -> str:
    image = source / relative
    image.parent.mkdir(parents=True, exist_ok=True)
    content = sample_id.encode("utf-8")
    image.write_bytes(content)
    stat = image.stat()
    with database.write_session() as session:
        session.add(
            Sample(
                id=sample_id,
                task_id=task.id,
                relative_path=relative,
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=hashlib.sha256(content).hexdigest(),
                pixel_sha256=(sample_id.encode("utf-8").hex() * 4)[:64],
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
    return sample_id


def test_r1031_sample_seen_balancing_rewrites_output_repeat_prefix(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    _add_sample_at(database, task, source, "repeat-cat-1", "2_cat/1.png")
    _add_sample_at(database, task, source, "repeat-cat-2", "2_cat/2.png")
    _add_sample_at(database, task, source, "plain-dog-1", "dogs/1.png")
    _add_sample_at(database, task, source, "plain-dog-2", "dogs/2.png")
    output = tmp_path / "repeat-export"
    output.mkdir()

    preview = _export_run_service()(database).preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        minimum_folder_images=1,
        add_repeat_prefix=True,
        sample_seen_mode="manual",
        sample_seen_target=6,
    )

    folders = {item["source_identifier"]: item for item in preview.folders}
    assert folders["2_cat"]["new_repeat"] == 3
    assert folders["2_cat"]["output_folder"] == "3_cat"
    assert folders["dogs"]["new_repeat"] == 3
    assert folders["dogs"]["output_folder"] == "3_dogs"


def test_r1031_sample_seen_warnings_cover_approximation_and_original_above_target(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    _add_sample_at(database, task, source, "repeat-two", "2_cat/image.png")
    _add_sample_at(database, task, source, "plain-dog-1", "dogs/1.png")
    _add_sample_at(database, task, source, "plain-dog-2", "dogs/2.png")
    output = tmp_path / "repeat-warning-export"
    output.mkdir()
    service = _export_run_service()(database)

    approximate = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        sample_seen_mode="manual",
        sample_seen_target=3,
    )
    folders = {item["source_identifier"]: item for item in approximate.folders}
    assert folders["dogs"]["new_repeat"] == 1
    assert folders["dogs"]["warning_codes"] == ["sample_seen_approximate"]

    above = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        sample_seen_mode="manual",
        sample_seen_target=1,
    )
    above_folder = {
        item["source_identifier"]: item for item in above.folders
    }["2_cat"]
    assert above_folder["new_repeat"] == 2
    assert above_folder["warning_codes"] == ["sample_seen_original_above_target"]


def test_r1031_folder_threshold_exclusion_is_not_counted_as_included(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    _add_sample_at(database, task, source, "kept-1", "kept/1.png")
    _add_sample_at(database, task, source, "kept-2", "kept/2.png")
    output = tmp_path / "threshold-count-export"
    output.mkdir()

    preview = _export_run_service()(database).preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        minimum_folder_images=2,
        add_repeat_prefix=True,
        sample_seen_mode="off",
        sample_seen_target=None,
    )

    assert preview.included_count == 2
    assert preview.exclusion_counts["included"] == 2
    assert preview.exclusion_counts["folder_below_minimum"] == 1
    assert preview.folder_below_minimum == {"folder_count": 1, "image_count": 1}


def test_r1031_preview_rejects_unicode_normalization_folder_collision(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    _add_sample_at(database, task, source, "composed-folder", "\u00e9/1.png")
    _add_sample_at(database, task, source, "decomposed-folder", "e\u0301/2.png")
    output = tmp_path / "unicode-collision-export"
    output.mkdir()

    with pytest.raises(ExportRunError) as error:
        _export_run_service()(database).preview(
            task.id,
            output_root=str(output),
            minimum_resolution=512,
            minimum_folder_images=1,
            add_repeat_prefix=True,
            sample_seen_mode="off",
            sample_seen_target=None,
        )

    assert error.value.code == "export_collision"


@pytest.mark.parametrize("unsafe_name", ("bad:name", "bad*name", "bad<name"))
def test_r1031_folder_name_rejects_windows_dangerous_characters(unsafe_name: str) -> None:
    planner_type = _export_run_planner()
    assert planner_type is not None

    with pytest.raises(ExportRunError) as error:
        planner_type._safe_folder_name(unsafe_name)

    assert error.value.code == "export_collision"


def test_r1031_source_relative_path_rejects_unsafe_deep_component() -> None:
    planner_type = _export_run_planner()
    assert planner_type is not None

    with pytest.raises(ExportRunError) as error:
        planner_type._source_folder("folder/bad:name/image.png", "source")

    assert error.value.code == "export_collision"


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("3_cat", (3, "cat")),
        ("01_cat", (1, "01_cat")),
        ("0_cat", (1, "0_cat")),
        ("cat", (1, "cat")),
    ),
)
def test_r1031_repeat_prefix_parser_accepts_only_positive_unpadded_prefix(
    value: str, expected: tuple[int, str]
) -> None:
    planner_type = _export_run_planner()
    assert planner_type is not None
    assert planner_type._parse_repeat(value) == expected


def test_r1031_dedupe_rejects_unicode_normalization_file_collision() -> None:
    planner_type = _export_run_planner()
    assert planner_type is not None
    files = [
        PlannedFile("1_cat/\u00e9.png", "a" * 64, 1, "source_image"),
        PlannedFile("1_cat/e\u0301.png", "b" * 64, 1, "source_image"),
    ]

    with pytest.raises(ExportRunError) as error:
        planner_type._dedupe_files(files)

    assert error.value.code == "export_collision"


def _aesthetic_identity(task) -> dict[str, str]:
    scoring = ScoringConfig.from_task_config(task.config)
    return {
        "source": EVIDENCE_SOURCES["aesthetic"],
        "model_id": scoring.aesthetic.model_id,
        "config_hash": scoring.inference_config_hash("aesthetic"),
        "algorithm_version": PREPROCESSING_VERSIONS["aesthetic"],
    }


def _add_aesthetic_evidence(
    database, task, sample_id: str, value, identity: dict[str, str]
) -> None:
    with database.write_session() as session:
        session.add(
            Evidence(
                task_id=task.id,
                sample_id=sample_id,
                code="aesthetic_score",
                source=identity["source"],
                value_json=value,
                threshold_json=None,
                value_number=value if isinstance(value, (int, float)) else None,
                threshold_number=None,
                metadata_json={
                    "model_id": identity["model_id"],
                    "config_hash": identity["config_hash"],
                },
                severity="info",
                review_only=False,
                bbox_json=None,
                algorithm_version=identity["algorithm_version"],
            )
        )


def test_export_run_plan_uses_current_broad_and_mutually_exclusive_aesthetic_summary(
    database, task_service, tmp_path
) -> None:
    planner_type = _export_run_planner()
    assert planner_type is not None
    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    sample_ids = ["export-run-sample"] + [
        _add_broad_sample(database, task, source, index) for index in range(1, 8)
    ]
    identity = _aesthetic_identity(task)
    _add_aesthetic_evidence(database, task, sample_ids[1], 2.5, identity)
    _add_aesthetic_evidence(database, task, sample_ids[3], float("nan"), identity)
    _add_aesthetic_evidence(database, task, sample_ids[4], 5.5, identity)
    mismatched = dict(identity)
    mismatched["config_hash"] = "m" * 64
    _add_aesthetic_evidence(database, task, sample_ids[5], 4.0, mismatched)
    _add_aesthetic_evidence(database, task, sample_ids[6], 3.0, identity)
    _add_aesthetic_evidence(database, task, sample_ids[6], 4.0, identity)
    _add_aesthetic_evidence(database, task, sample_ids[7], 4.0, identity)
    with database.write_session() as session:
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id=sample_ids[0],
                scope_type="sample",
                scope_id=sample_ids[0],
                category=MANUAL_EXCLUSION_CATEGORY,
                decision=MANUAL_EXCLUSION_DECISION,
                source="human",
                context_json={},
                supersedes_id=None,
                is_active=True,
            )
        )
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id=sample_ids[1],
                scope_type="sample",
                scope_id=sample_ids[1],
                category="aesthetic",
                decision="approved_keep",
                source="human",
                context_json={},
                supersedes_id=None,
                is_active=True,
            )
        )
    output = tmp_path / "threshold-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        aesthetic_minimum=3.0,
    )

    planned = planner_type(database).build(run.id)

    assert planned.summary["exclusion_counts"] == {
        "included": 2,
        "resolution_below_minimum": 0,
        "manual_exclude": 1,
        "folder_below_minimum": 0,
        "aesthetic_below_minimum": 0,
        "domain_below_minimum": 0,
        "domain_missing": 0,
        "domain_non_finite": 0,
        "domain_out_of_range": 0,
        "domain_provenance_mismatch": 0,
        "domain_ambiguous": 0,
        "aesthetic_missing": 1,
        "aesthetic_non_finite": 1,
        "aesthetic_out_of_range": 1,
        "aesthetic_provenance_mismatch": 1,
        "aesthetic_ambiguous": 1,
        "style_outlier": 0,
        "style_missing": 0,
        "style_non_finite": 0,
        "style_out_of_range": 0,
        "style_provenance_mismatch": 0,
        "style_ambiguous": 0,
        "duplicate_non_representative": 0,
    }
    assert planned.summary["included_count"] == 2
    assert planned.summary["folders"][0]["source_identifier"] == "samples"
    assert planned.summary["folders"][0]["image_count"] == 2
    assert planned.summary["folders"][0]["output_folder"] == "1_samples"
    assert planned.summary["folders"][0]["warning_codes"] == []
    assert len(planned.plan.files) == 2


def test_export_run_plan_excludes_all_active_human_curated_exclusions(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    excluded = [
        _add_broad_sample(database, task, source, index) for index in range(1, 4)
    ]
    with database.write_session() as session:
        for sample_id, category in zip(
            excluded,
            ("curated:risk", "curated:style_outlier", "curated:duplicate"),
            strict=True,
        ):
            session.add(
                ReviewDecision(
                    task_id=task.id,
                    sample_id=sample_id,
                    scope_type="sample",
                    scope_id=sample_id,
                    category=category,
                    decision="approved_exclude",
                    source="human",
                    context_json={},
                    supersedes_id=None,
                    is_active=True,
                )
    )
    output = tmp_path / "curated-exclusions-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )

    planned = _export_run_planner()(database).build(run.id)

    destinations = {file.destination_relative for file in planned.plan.files}
    assert all(
        not any(
            destination.endswith(f"/samples/{index}.png")
            for destination in destinations
        )
        for index in range(1, 4)
    )
    assert planned.summary["exclusion_counts"]["manual_exclude"] == 3
    assert planned.summary["exclusion_counts"]["included"] == 1


def test_shared_worker_slot_prioritizes_tasks_and_recovers_export_run_lease(
    database, task_service, tmp_path
) -> None:
    from dataset_audit_studio.jobs.types import ClaimedExportRun

    completed = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "leased-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        completed.id, output_root=str(output), minimum_resolution=512
    )
    draft = task_service.create_task(
        name="ordinary task wins",
        source_root=str(tmp_path / "queued-source"),
        output_root=str(tmp_path / "queued-output"),
        config=ComponentTaskConfigMaterializer().materialize(
            materialize_profile("general")["components"],
            profile="general",
            require_profile=True,
        ),
    )
    queued = task_service.queue_task(draft.id, expected_version=draft.row_version)
    claimed_task = task_service.claim_next(owner="shared-worker", lease_seconds=60)

    assert claimed_task is not None
    assert claimed_task.task.id == queued.id
    with database.write_session() as session:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        session.delete(lease)
        row = session.get(Task, queued.id)
        assert row is not None
        row.status = TaskStatus.COMPLETED.value
    claimed_run = task_service.claim_next(owner="shared-worker", lease_seconds=60)

    assert isinstance(claimed_run, ClaimedExportRun)
    assert claimed_run.export_run_id == run.id
    with database.write_session() as session:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        assert lease.task_id is None
        assert lease.export_run_id == run.id
        lease.expires_at = task_service.clock() - timedelta(seconds=1)
    task_service.recover_stale_leases()

    assert _export_run_service()(database).get(run.id).status == "queued"


def test_export_run_claim_can_be_requeued_when_no_executor_is_composed(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "uncomposed-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="task-only-worker", lease_seconds=60)
    assert claimed is not None

    task_service.requeue_export_run(claimed.token)

    assert _export_run_service()(database).get(run.id).status == "queued"
    with database.read_session() as session:
        assert session.get(WorkerLease, 1) is None


def _export_run_executor():
    try:
        module = importlib.import_module("dataset_audit_studio.export_runs.executor")
    except ModuleNotFoundError:
        return None
    return getattr(module, "ExportRunExecutor", None)


def test_copy_export_run_publishes_manifest_and_recovers_after_publish_before_commit(
    database, task_service, tmp_path
) -> None:
    executor_type = _export_run_executor()
    assert executor_type is not None
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "copy-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="copy-worker", lease_seconds=60)
    assert claimed is not None

    completed = executor_type(database).run(claimed.token)

    assert completed.status == "completed", (completed.error_code, completed.error_message)
    assert (output / "1_source" / "sample.png").read_bytes() == b"export-run-source"
    manifest = output / "export-run-manifest.json"
    assert completed.manifest_path == str(manifest)
    assert completed.manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()
    with database.write_session() as session:
        row = session.get(models.ExportRun, run.id)
        assert row is not None
        row.status = "publishing"
        row.manifest_path = None
        row.manifest_sha256 = None
        row.completed_at = None
        row.execution_epoch += 1
        assert session.get(WorkerLease, 1) is None
        session.add(
            WorkerLease(
                slot_id=1,
                task_id=None,
                export_run_id=row.id,
                owner="recovery-worker",
                execution_epoch=row.execution_epoch,
                acquired_at=task_service.clock(),
                heartbeat_at=task_service.clock(),
                expires_at=task_service.clock() + timedelta(seconds=60),
            )
        )
        session.flush()
        recovery_epoch = row.execution_epoch

    recovered = executor_type(database).run(
        importlib.import_module("dataset_audit_studio.jobs.types").ExportRunToken(
            run.id, "recovery-worker", recovery_epoch
        )
    )

    assert recovered.status == "completed", (recovered.error_code, recovered.error_message)
    assert recovered.manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()
    with database.read_session() as session:
        assert session.get(WorkerLease, 1) is None


def test_export_run_execution_preserves_protected_task_records(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "protected-records-export"
    output.mkdir()
    _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )

    def snapshot() -> tuple[object, ...]:
        with database.read_session() as session:
            row = session.get(Task, task.id)
            assert row is not None
            return (
                row.status,
                row.current_config_revision,
                row.row_version,
                row.execution_epoch,
                session.query(models.TaskConfig).filter_by(task_id=task.id).count(),
                session.query(Evidence).filter_by(task_id=task.id).count(),
                session.query(ReviewDecision).filter_by(task_id=task.id).count(),
                session.query(models.TaskEvent).filter_by(task_id=task.id).count(),
            )

    before = snapshot()
    claimed = task_service.claim_next(owner="protected-records-worker", lease_seconds=60)
    assert claimed is not None
    completed = _export_run_executor()(database).run(claimed.token)

    assert completed.status == "completed", (completed.error_code, completed.error_message)
    assert snapshot() == before


def test_export_run_recovers_staging_created_before_a_hard_stop(
    database, task_service, tmp_path
) -> None:
    class HardStop(BaseException):
        pass

    class StopAfterStagingPublisher(ExportTreePublisher):
        def prepare_directories(self, *args, **kwargs) -> None:
            super().prepare_directories(*args, **kwargs)
            raise HardStop("simulated process stop")

    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "staging-recovery"
    output.mkdir()
    _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="interrupted-worker", lease_seconds=60)
    assert claimed is not None

    with pytest.raises(HardStop):
        _export_run_executor()(database, tree_publisher=StopAfterStagingPublisher()).run(
            claimed.token
        )
    with database.write_session() as session:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        lease.expires_at = task_service.clock() - timedelta(seconds=1)
    task_service.recover_stale_leases()
    resumed = task_service.claim_next(owner="recovery-worker", lease_seconds=60)
    assert resumed is not None

    completed = _export_run_executor()(database).run(resumed.token)

    assert completed.status == "completed", (completed.error_code, completed.error_message)
    assert (output / "1_source" / "sample.png").read_bytes() == b"export-run-source"


def test_export_run_recovers_staging_with_selected_image_format(
    database, task_service, tmp_path
) -> None:
    class HardStop(BaseException):
        pass

    class StopAfterStagingPublisher(ExportTreePublisher):
        def prepare_directories(self, *args, **kwargs) -> None:
            super().prepare_directories(*args, **kwargs)
            raise HardStop("simulated process stop")

    buffer = BytesIO()
    source_image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    source_image.save(buffer, format="PNG")
    source_image.close()
    task = _completed_profile_task(
        database, task_service, tmp_path, image_bytes=buffer.getvalue()
    )
    output = tmp_path / "converted-staging-recovery"
    output.mkdir()
    _export_run_service()(database).create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
    )
    claimed = task_service.claim_next(owner="converted-interrupted-worker", lease_seconds=60)
    assert claimed is not None

    with pytest.raises(HardStop):
        _export_run_executor()(database, tree_publisher=StopAfterStagingPublisher()).run(
            claimed.token
        )
    with database.write_session() as session:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        lease.expires_at = task_service.clock() - timedelta(seconds=1)
    task_service.recover_stale_leases()
    resumed = task_service.claim_next(owner="converted-recovery-worker", lease_seconds=60)
    assert resumed is not None

    completed = _export_run_executor()(database).run(resumed.token)

    assert completed.status == "completed", (completed.error_code, completed.error_message)
    with Image.open(output / "1_source" / "sample.webp") as exported:
        assert exported.mode == "RGBA"
        assert exported.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize(
    ("tamper_manifest", "expected_status", "expected_error"),
    (
        (False, "completed", None),
        (True, "failed", "export_output_changed"),
    ),
)
def test_export_run_recovers_published_tree_without_source_access(
    tamper_manifest,
    expected_status,
    expected_error,
    database,
    task_service,
    tmp_path,
) -> None:
    class HardStop(BaseException):
        pass

    class StopAfterPublishPublisher(ExportTreePublisher):
        def publish_tree(self, *args, **kwargs) -> None:
            super().publish_tree(*args, **kwargs)
            raise HardStop("simulated stop after atomic publish")

    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "published-recovery"
    output.mkdir()
    _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="publish-stop-worker", lease_seconds=60)
    assert claimed is not None
    with pytest.raises(HardStop):
        _export_run_executor()(database, tree_publisher=StopAfterPublishPublisher()).run(
            claimed.token
        )
    manifest = output / "export-run-manifest.json"
    assert manifest.is_file()
    if tamper_manifest:
        manifest.write_bytes(manifest.read_bytes() + b"tampered")
    Path(task.source_root, "sample.png").unlink()
    with database.write_session() as session:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        lease.expires_at = task_service.clock() - timedelta(seconds=1)
    task_service.recover_stale_leases()
    resumed = task_service.claim_next(owner="published-recovery-worker", lease_seconds=60)
    assert resumed is not None

    recovered = _export_run_executor()(database).run(resumed.token)

    assert recovered.status == expected_status
    assert recovered.error_code == expected_error


def test_export_run_rejects_self_consistent_published_tree_replacement_without_source(
    database, task_service, tmp_path
) -> None:
    class HardStop(BaseException):
        pass

    class StopAfterPublishPublisher(ExportTreePublisher):
        def publish_tree(self, *args, **kwargs) -> None:
            super().publish_tree(*args, **kwargs)
            raise HardStop("simulated stop after atomic publish")

    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "self-consistent-published-recovery"
    output.mkdir()
    _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="self-consistent-stop-worker", lease_seconds=60)
    assert claimed is not None
    with pytest.raises(HardStop):
        _export_run_executor()(database, tree_publisher=StopAfterPublishPublisher()).run(
            claimed.token
        )

    manifest = output / "export-run-manifest.json"
    payload = json.loads(manifest.read_bytes())
    published_file = output / "1_source" / "sample.png"
    replacement = b"self-consistent-replacement"
    published_file.write_bytes(replacement)
    entry = next(item for item in payload["files"] if item["path"] == "1_source/sample.png")
    entry["sha256"] = hashlib.sha256(replacement).hexdigest()
    entry["size"] = len(replacement)
    manifest.write_bytes(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )
    Path(task.source_root, "sample.png").unlink()
    with database.write_session() as session:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        lease.expires_at = task_service.clock() - timedelta(seconds=1)
    task_service.recover_stale_leases()
    resumed = task_service.claim_next(owner="self-consistent-recovery-worker", lease_seconds=60)
    assert resumed is not None

    recovered = _export_run_executor()(database).run(resumed.token)

    assert recovered.status == "failed"
    assert recovered.error_code == "export_output_changed"
    assert published_file.read_bytes() == replacement


def test_export_run_recovery_rejects_frozen_settings_tamper(
    database, task_service, tmp_path
) -> None:
    class HardStop(BaseException):
        pass

    class StopAfterPublishPublisher(ExportTreePublisher):
        def publish_tree(self, *args, **kwargs) -> None:
            super().publish_tree(*args, **kwargs)
            raise HardStop("simulated stop after atomic publish")

    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "settings-tamper-recovery"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="settings-tamper-stop-worker", lease_seconds=60)
    assert claimed is not None
    with pytest.raises(HardStop):
        _export_run_executor()(database, tree_publisher=StopAfterPublishPublisher()).run(
            claimed.token
        )

    with database.write_session() as session:
        row = session.get(models.ExportRun, run.id)
        assert row is not None
        settings = dict(row.settings_json)
        settings["mode"] = "rewrite"
        row.settings_json = settings
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        lease.expires_at = task_service.clock() - timedelta(seconds=1)
    task_service.recover_stale_leases()
    resumed = task_service.claim_next(owner="settings-tamper-recovery-worker", lease_seconds=60)
    assert resumed is not None

    recovered = _export_run_executor()(database).run(resumed.token)

    assert recovered.status == "failed"
    assert recovered.error_code == "export_output_changed"


def test_export_run_requeues_an_expired_lease_after_a_long_copy(
    database, task_service, tmp_path
) -> None:
    class ExpireAfterCopyPublisher(ExportTreePublisher):
        def __init__(self) -> None:
            super().__init__()
            self.expired = False

        def write_file(self, staging_root, file) -> None:
            super().write_file(staging_root, file)
            if not self.expired:
                self.expired = True
                with database.write_session() as session:
                    lease = session.get(WorkerLease, 1)
                    assert lease is not None
                    lease.expires_at = task_service.clock() - timedelta(seconds=1)

    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "lease-expiry-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="long-copy-worker", lease_seconds=60)
    assert claimed is not None

    requeued = _export_run_executor()(database, tree_publisher=ExpireAfterCopyPublisher()).run(
        claimed.token
    )

    assert requeued.status == "queued"
    assert requeued.error_code is None
    assert requeued.checkpoint["staging_owner"] == run.id
    with database.read_session() as session:
        assert session.get(WorkerLease, 1) is None
    resumed = task_service.claim_next(owner="long-copy-recovery-worker", lease_seconds=60)
    assert resumed is not None
    completed = _export_run_executor()(database).run(resumed.token)
    assert completed.status == "completed", (completed.error_code, completed.error_message)


def test_export_run_api_validates_and_lists_newest_first(tmp_path) -> None:
    from dataset_audit_studio.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(
        database_path=tmp_path / "api.db",
        project_root=tmp_path,
        enforce_runtime=False,
    )
    with TestClient(app) as client:
        tasks = TaskService(app.state.database)
        task = _completed_profile_task(app.state.database, tasks, tmp_path)
        invalid_root = tmp_path / "invalid"
        invalid_root.mkdir()
        invalid = client.post(
            f"/api/tasks/{task.id}/export-runs",
            json={
                "output_root": str(invalid_root),
                "minimum_resolution": 512,
                "aesthetic_minimum": 1.2,
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "export_aesthetic_minimum_invalid"
        first_root = tmp_path / "api-export-one"
        first_root.mkdir()
        first_preview = client.post(
            f"/api/tasks/{task.id}/export-runs/preview",
            json={
                "output_root": str(first_root),
                "minimum_resolution": 512,
                "image_format": "original",
            },
        )
        assert first_preview.status_code == 200
        first = client.post(
            f"/api/tasks/{task.id}/export-runs",
            json={
                "output_root": str(first_root),
                "minimum_resolution": 512,
                "minimum_folder_images": 1,
                "add_repeat_prefix": True,
                "sample_seen_mode": "off",
                "sample_seen_target": None,
                "image_format": "original",
                "preview_digest": first_preview.json()["preview_digest"],
            },
        )
        assert first.status_code == 202
        assert first.json()["status"] == "queued"
        second_root = tmp_path / "api-export-two"
        second_root.mkdir()
        second_preview = client.post(
            f"/api/tasks/{task.id}/export-runs/preview",
            json={"output_root": str(second_root), "minimum_resolution": 512},
        )
        second = client.post(
            f"/api/tasks/{task.id}/export-runs",
            json={
                "output_root": str(second_root),
                "minimum_resolution": 512,
                "preview_digest": second_preview.json()["preview_digest"],
            },
        )
        assert second.status_code == 202

        page = client.get(f"/api/tasks/{task.id}/export-runs", params={"offset": 0, "limit": 1})

        assert page.status_code == 200
        assert page.json()["total"] == 2
        assert page.json()["items"][0]["id"] == second.json()["id"]
        missing = client.get("/api/tasks/not-a-task/export-runs")
        assert missing.status_code == 404
        assert missing.json()["code"] == "task_not_found"


def test_r12_export_run_api_validates_new_eligibility_settings_and_forbids_extra(tmp_path) -> None:
    from dataset_audit_studio.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(
        database_path=tmp_path / "r12-api.db",
        project_root=tmp_path,
        enforce_runtime=False,
    )
    with TestClient(app) as client:
        tasks = TaskService(app.state.database)
        task = _completed_profile_task(app.state.database, tasks, tmp_path)
        output = tmp_path / "r12-api-output"
        output.mkdir()
        invalid = client.post(
            f"/api/tasks/{task.id}/export-runs/preview",
            json={
                "output_root": str(output),
                "minimum_resolution": 512,
                "domain_minimum": True,
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "export_domain_minimum_invalid"
        extra = client.post(
            f"/api/tasks/{task.id}/export-runs/preview",
            json={
                "output_root": str(output),
                "minimum_resolution": 512,
                "unknown_r12_field": "forbidden",
            },
        )
        assert extra.status_code == 422
        valid = client.post(
            f"/api/tasks/{task.id}/export-runs/preview",
            json={"output_root": str(output), "minimum_resolution": 512},
        )
        assert valid.status_code == 200
        assert valid.json()["domain_minimum"] is None
        assert valid.json()["exclude_exact_visual_duplicates"] is False
        assert valid.json()["style_outlier_mode"] == "off"
        invalid_format = client.post(
            f"/api/tasks/{task.id}/export-runs/preview",
            json={
                "output_root": str(output),
                "minimum_resolution": 512,
                "image_format": "tiff",
            },
        )
        assert invalid_format.status_code == 422
        assert invalid_format.json()["code"] == "export_image_format_invalid"
        invalid_format_type = client.post(
            f"/api/tasks/{task.id}/export-runs/preview",
            json={
                "output_root": str(output),
                "minimum_resolution": 512,
                "image_format": ["webp"],
            },
        )
        assert invalid_format_type.status_code == 422
        assert invalid_format_type.json()["code"] == "export_image_format_invalid"


def test_local_worker_dispatches_export_claim_without_task_runner(tmp_path) -> None:
    runner_source = (
        Path(__file__).parents[1] / "backend" / "dataset_audit_studio" / "jobs" / "runner.py"
    ).read_text(encoding="utf-8")

    assert "export_run_runner: ExportRunRunner | None = None" in runner_source
    assert "isinstance(claimed, ClaimedExportRun)" in runner_source
    assert "self.export_run_runner.run(claimed.token)" in runner_source
    assert "service.requeue_export_run(claimed.token)" in runner_source


def test_copy_export_run_excludes_legacy_latent_files(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    Path(task.source_root, "sample.npz").write_bytes(b"latent")
    output = tmp_path / "latent-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )

    planned = _export_run_planner()(database).build(run.id)

    destinations = {file.destination_relative for file in planned.plan.files}
    assert "1_source/sample.npz" not in destinations


def test_r1031_repeat_mapping_applies_to_annotation_without_legacy_latent_artifacts(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    source = Path(task.source_root)
    _add_sample_at(database, task, source, "mapped-sample", "2_cat/image.png")
    (source / "2_cat" / "image.txt").write_bytes(b"caption")
    (source / "2_cat" / "image.npz").write_bytes(b"latent")
    output = tmp_path / "mapped-artifacts-export"
    output.mkdir()

    preview = _export_run_service()(database).preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        minimum_folder_images=1,
        add_repeat_prefix=True,
        sample_seen_mode="manual",
        sample_seen_target=4,
    )
    folders = {item["source_identifier"]: item for item in preview.folders}
    assert folders["2_cat"]["output_folder"] == "4_cat"
    run = _export_run_service()(database).create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        minimum_folder_images=1,
        add_repeat_prefix=True,
        sample_seen_mode="manual",
        sample_seen_target=4,
        preview_digest=preview.preview_digest,
    )
    planned = _export_run_planner()(database).build(run.id)

    destinations = {file.destination_relative for file in planned.plan.files}
    assert "4_cat/image.png" in destinations
    assert "4_cat/image.txt" in destinations
    assert "4_cat/image.npz" not in destinations


@pytest.mark.parametrize("output_change", ("missing", "nonempty"))
def test_terminal_export_failure_preserves_unproven_staging(
    output_change,
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "changed-output"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    staging = output.parent / f".{output.name}.export-run-{run.id}.staging"
    staging.mkdir()
    (staging / "partial.bin").write_bytes(b"partial")
    marker = output / "external.txt"
    if output_change == "missing":
        output.rmdir()
    else:
        marker.write_bytes(b"external")
    claimed = task_service.claim_next(owner="failure-worker", lease_seconds=60)
    assert claimed is not None

    failed = _export_run_executor()(database).run(claimed.token)

    assert failed.status == "failed"
    assert failed.error_code == "export_output_changed"
    if output_change == "nonempty":
        assert marker.read_bytes() == b"external"
    else:
        assert not output.exists()
    assert staging.exists()
    assert (staging / "partial.bin").read_bytes() == b"partial"


def test_terminal_export_failure_cleans_checkpoint_owned_staging(
    database, task_service, tmp_path
) -> None:
    class FailingPublisher(ExportTreePublisher):
        def write_file(self, *args, **kwargs) -> None:
            raise RuntimeError("Export staging became unsafe")

    task = _completed_profile_task(database, task_service, tmp_path)
    output = tmp_path / "owned-staging-export"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    staging = output.parent / f".{output.name}.export-run-{run.id}.staging"
    claimed = task_service.claim_next(owner="owned-staging-worker", lease_seconds=60)
    assert claimed is not None

    failed = _export_run_executor()(database, tree_publisher=FailingPublisher()).run(
        claimed.token
    )

    assert failed.status == "failed"
    assert failed.checkpoint["staging_owner"] == run.id
    assert not staging.exists()


def test_export_run_rejects_a_raw_reparse_output_alias_before_resolution(
    database, task_service, tmp_path, monkeypatch
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    target = tmp_path / "reparse-target"
    target.mkdir()
    alias = tmp_path / "reparse-alias"
    service_module = importlib.import_module("dataset_audit_studio.export_runs.service")
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        # Windows hosts without link privileges still exercise the conservative
        # pre-resolve guard without claiming that an OS link was created.
        alias.mkdir()
        monkeypatch.setattr(
            service_module,
            "is_reparse",
            lambda path: path == alias,
            raising=False,
        )

    with pytest.raises(ExportRunError) as error:
        _export_run_service()(database).create(
            task.id,
            output_root=str(alias),
            minimum_resolution=512,
        )

    assert error.value.code == "export_output_path_invalid"
    assert list(target.iterdir()) == []


def test_export_create_does_not_encode_inside_write_session(
    database, task_service, tmp_path, monkeypatch
) -> None:
    encode_in_write = _track_encode_inside_write_session(monkeypatch)
    task = _completed_profile_task(
        database, task_service, tmp_path, image_bytes=_png_rgba_bytes()
    )
    output = tmp_path / "encode-outside-write"
    output.mkdir()
    service = _export_run_service()(database)
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
    )

    service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
        preview_digest=preview.preview_digest,
    )

    assert encode_in_write
    assert encode_in_write == [False] * len(encode_in_write)


def test_planner_preview_digest_has_no_dead_false_branch() -> None:
    source = inspect.getsource(
        importlib.import_module("dataset_audit_studio.export_runs.planner")
    )

    assert "if False" not in source


def test_export_service_uses_public_planner_contract() -> None:
    source = inspect.getsource(
        importlib.import_module("dataset_audit_studio.export_runs.service")
    )

    assert "planner._plan_current" not in source
    assert "planner._finalize_plan" not in source
    assert "planner._plan_fingerprint" not in source


def test_transcode_cache_hit_avoids_second_encode(tmp_path, monkeypatch) -> None:
    from dataset_audit_studio.export.image_conversion import encode_export_image
    from dataset_audit_studio.export_runs.transcode_cache import (
        TranscodeCache,
        transcode_cache_key,
    )

    source = tmp_path / "cache-source.png"
    source.write_bytes(_png_rgba_bytes())
    original = encode_export_image
    calls = {"n": 0}

    def counting(path, image_format):
        calls["n"] += 1
        return original(path, image_format)

    monkeypatch.setattr(
        "dataset_audit_studio.export_runs.transcode_cache.encode_export_image",
        counting,
    )
    cache = TranscodeCache()
    first = cache.encode(source, "webp")
    second = cache.encode(source, "webp")

    assert first == second
    assert calls["n"] == 1
    assert transcode_cache_key(source, "jpeg") != transcode_cache_key(source, "webp")


def test_transcode_cache_does_not_reuse_mismatched_format_or_mtime(
    tmp_path, monkeypatch
) -> None:
    from dataset_audit_studio.export.image_conversion import encode_export_image
    from dataset_audit_studio.export_runs.transcode_cache import TranscodeCache

    source = tmp_path / "cache-mismatch.png"
    source.write_bytes(_png_rgba_bytes())
    original = encode_export_image
    calls = {"n": 0}

    def counting(path, image_format):
        calls["n"] += 1
        return original(path, image_format)

    monkeypatch.setattr(
        "dataset_audit_studio.export_runs.transcode_cache.encode_export_image",
        counting,
    )
    cache = TranscodeCache()
    webp = cache.encode(source, "webp")
    source.write_bytes(_png_rgba_bytes())
    jpeg = cache.encode(source, "jpeg")
    other = Image.new("RGB", (3, 1), (9, 8, 7))
    other.save(source, format="PNG")
    other.close()
    changed = cache.encode(source, "webp")

    assert webp != jpeg
    assert changed == cache.encode(source, "webp")
    assert calls["n"] == 3


def test_export_run_reuses_transcode_cache_from_preview_to_publish(
    database, task_service, tmp_path, monkeypatch
) -> None:
    from dataset_audit_studio.export.image_conversion import encode_export_image as original

    calls = {"n": 0}

    def counting(source, image_format):
        calls["n"] += 1
        return original(source, image_format)

    monkeypatch.setattr(
        "dataset_audit_studio.export.image_conversion.encode_export_image",
        counting,
    )
    monkeypatch.setattr(
        "dataset_audit_studio.export.tree_publisher.encode_export_image",
        counting,
    )
    monkeypatch.setattr(
        "dataset_audit_studio.export_runs.transcode_cache.encode_export_image",
        counting,
    )
    task = _completed_profile_task(
        database, task_service, tmp_path, image_bytes=_png_rgba_bytes()
    )
    output = tmp_path / "cached-transcode"
    output.mkdir()
    service = _export_run_service()(database)
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
    )
    service.create(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        image_format="webp",
        preview_digest=preview.preview_digest,
    )
    claimed = task_service.claim_next(owner="cache-hit-worker", lease_seconds=60)
    assert claimed is not None
    completed = _export_run_executor()(database).run(claimed.token)

    assert completed.status == "completed", completed.error_message
    assert calls["n"] == 1


def test_export_executor_heartbeat_interval_defaults_to_batch_size() -> None:
    executor_mod = importlib.import_module("dataset_audit_studio.export_runs.executor")

    assert 32 <= executor_mod.HEARTBEAT_FILE_INTERVAL <= 64


def test_export_executor_heartbeats_in_file_batches(
    database, task_service, tmp_path, monkeypatch
) -> None:
    copying_cursors: list[int] = []
    executor_type = _export_run_executor()
    original = executor_type._set_copying

    def tracking(self, token, staging_root, next_file, plan, manifest_sha256):
        copying_cursors.append(next_file)
        return original(self, token, staging_root, next_file, plan, manifest_sha256)

    monkeypatch.setattr(executor_type, "_set_copying", tracking)
    task = _completed_profile_task(
        database, task_service, tmp_path, caption="batch heartbeat caption"
    )
    output = tmp_path / "heartbeat-batch"
    output.mkdir()
    _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="heartbeat-batch-worker", lease_seconds=60)
    assert claimed is not None
    completed = executor_type(database).run(claimed.token)

    assert completed.status == "completed", completed.error_message
    assert 1 not in copying_cursors
    assert copying_cursors[-1] >= 2


def test_export_run_resume_is_idempotent_when_staging_ahead_of_checkpoint(
    database, task_service, tmp_path
) -> None:
    class HardStop(BaseException):
        pass

    class StopAfterSecondFile(ExportTreePublisher):
        def __init__(self) -> None:
            super().__init__()
            self.writes = 0

        def write_file(self, staging_root, file) -> None:
            super().write_file(staging_root, file)
            self.writes += 1
            if self.writes >= 2:
                raise HardStop("stop after second staging write")

    task = _completed_profile_task(
        database, task_service, tmp_path, caption="resume caption"
    )
    output = tmp_path / "uncheckpointed-staging"
    output.mkdir()
    run = _export_run_service()(database).create(
        task.id, output_root=str(output), minimum_resolution=512
    )
    claimed = task_service.claim_next(owner="uncheckpointed-worker", lease_seconds=60)
    assert claimed is not None
    with pytest.raises(HardStop):
        _export_run_executor()(database, tree_publisher=StopAfterSecondFile()).run(
            claimed.token
        )
    with database.read_session() as session:
        persisted = session.get(models.ExportRun, run.id)
        assert persisted is not None
        assert persisted.checkpoint_json.get("next_file") == 0
    with database.write_session() as session:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        lease.expires_at = task_service.clock() - timedelta(seconds=1)
    task_service.recover_stale_leases()
    resumed = task_service.claim_next(owner="uncheckpointed-recovery", lease_seconds=60)
    assert resumed is not None

    completed = _export_run_executor()(database).run(resumed.token)

    assert completed.status == "completed", (completed.error_code, completed.error_message)
    assert (output / "1_source" / "sample.png").read_bytes() == b"export-run-source"
    assert (output / "1_source" / "sample.txt").read_text(encoding="utf-8") == "resume caption"
