from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import Evidence, ReviewDecision, Sample, Task, TaskConfig
from dataset_audit_studio.export_runs.eligibility import EligibilityResolver
from dataset_audit_studio.jobs.service import TaskService
from sqlalchemy import event, select


def _completed_profile_task(
    database,
    task_service: TaskService,
    tmp_path: Path,
) -> object:
    source = tmp_path / "source"
    source.mkdir()
    components = materialize_profile("general")["components"]
    components["media.scan"]["config"]["resolutions"] = [512, 1024]
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
    image.write_bytes(b"export-run-source")
    stat = image.stat()
    with database.write_session() as session:
        session.add(
            Sample(
                id="export-run-sample",
                task_id=task.id,
                relative_path="sample.png",
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=hashlib.sha256(b"export-run-source").hexdigest(),
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


def _export_run_service(database):
    module = importlib.import_module("dataset_audit_studio.export_runs.service")
    service_type = module.ExportRunService
    return service_type(database)


def _add_scope_sample(
    database,
    task,
    *,
    sample_id: str,
    relative_path: str,
    pixel_sha256: str,
    artist_scope: str = "artist_a",
) -> None:
    with database.write_session() as session:
        session.add(
            Sample(
                id=sample_id,
                task_id=task.id,
                relative_path=relative_path,
                source_size=1,
                source_mtime_ns=1,
                source_sha256=sample_id,
                pixel_sha256=pixel_sha256,
                media_kind="image",
                artist_scope=artist_scope,
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


def _expected_scope_hash(
    *,
    scope_id: str,
    members: list[Sample],
    config: StyleConfig,
) -> str:
    payload = {
        "scope_id": scope_id,
        "samples": [
            [sample.id, sample.pixel_sha256 or ""]
            for sample in sorted(members, key=lambda row: (row.relative_path, row.id))
        ],
        "analysis": config.analysis_payload(),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def test_style_scope_identities_exclude_ai_and_domain_misses_from_scope_hash(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    _add_scope_sample(
        database,
        task,
        sample_id="scope-included",
        relative_path="a/included.png",
        pixel_sha256="a" * 64,
    )
    _add_scope_sample(
        database,
        task,
        sample_id="scope-ai-excluded",
        relative_path="a/ai.png",
        pixel_sha256="b" * 64,
    )
    _add_scope_sample(
        database,
        task,
        sample_id="scope-domain-miss",
        relative_path="a/domain.png",
        pixel_sha256="c" * 64,
    )
    with database.write_session() as session:
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id="scope-ai-excluded",
                scope_type="sample",
                scope_id="scope-ai-excluded",
                category="ai_generated",
                decision="approved_exclude",
                source="human",
                context_json={},
                supersedes_id=None,
                is_active=True,
            )
        )
        session.add(
            Evidence(
                task_id=task.id,
                sample_id="scope-domain-miss",
                code="in_domain_probability",
                source="test",
                value_json=0.2,
                threshold_json=0.5,
                value_number=0.2,
                threshold_number=0.5,
                metadata_json={},
                severity="info",
                review_only=False,
                bbox_json=None,
                algorithm_version="test",
            )
        )

    style_config = StyleConfig.from_task_config(task.config)
    with database.read_session() as session:
        included = session.get(Sample, "scope-included")
        assert included is not None
        expected_hash = _expected_scope_hash(
            scope_id="artist_a",
            members=[included],
            config=style_config,
        )
        identities = EligibilityResolver._style_scope_identities(
            session, task_id=task.id, config=style_config
        )

    assert identities["scope-included"] == (expected_hash, 1)
    assert "scope-ai-excluded" not in identities
    assert "scope-domain-miss" not in identities


def test_style_scope_identities_does_not_materialize_exclude_sets(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    _add_scope_sample(
        database,
        task,
        sample_id="sql-included",
        relative_path="b/included.png",
        pixel_sha256="d" * 64,
    )
    _add_scope_sample(
        database,
        task,
        sample_id="sql-ai-excluded",
        relative_path="b/ai.png",
        pixel_sha256="e" * 64,
    )
    with database.write_session() as session:
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id="sql-ai-excluded",
                scope_type="sample",
                scope_id="sql-ai-excluded",
                category="ai_generated",
                decision="approved_exclude",
                source="human",
                context_json={},
                supersedes_id=None,
                is_active=True,
            )
        )

    standalone_queries: dict[str, int] = {"review_decisions": 0, "evidence": 0}

    def capture(_, __, statement, ___, ____, _____) -> None:
        sql = statement.casefold()
        if "from review_decisions" in sql and "from samples" not in sql:
            standalone_queries["review_decisions"] += 1
        if (
            "from evidence" in sql
            and "in_domain_probability" in sql
            and "from samples" not in sql
        ):
            standalone_queries["evidence"] += 1

    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        with database.read_session() as session:
            EligibilityResolver._style_scope_identities(
                session,
                task_id=task.id,
                config=StyleConfig.from_task_config(task.config),
            )
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)

    assert standalone_queries == {"review_decisions": 0, "evidence": 0}


def test_style_scope_identities_preserves_eligibility_digest_with_excludes(
    database, task_service, tmp_path
) -> None:
    task = _completed_profile_task(database, task_service, tmp_path)
    _add_scope_sample(
        database,
        task,
        sample_id="digest-included",
        relative_path="c/included.png",
        pixel_sha256="f" * 64,
    )
    _add_scope_sample(
        database,
        task,
        sample_id="digest-ai-excluded",
        relative_path="c/ai.png",
        pixel_sha256="0" * 64,
    )
    with database.write_session() as session:
        session.add(
            ReviewDecision(
                task_id=task.id,
                sample_id="digest-ai-excluded",
                scope_type="sample",
                scope_id="digest-ai-excluded",
                category="ai_generated",
                decision="approved_exclude",
                source="human",
                context_json={},
                supersedes_id=None,
                is_active=True,
            )
        )
    output = tmp_path / "style-sql-filter-digest"
    output.mkdir()
    service = _export_run_service(database)
    preview = service.preview(
        task.id,
        output_root=str(output),
        minimum_resolution=512,
        style_outlier_mode="strong",
    )

    style_config = StyleConfig.from_task_config(task.config)
    with database.read_session() as session:
        included = session.get(Sample, "digest-included")
        assert included is not None
        expected_hash = _expected_scope_hash(
            scope_id="artist_a",
            members=[included],
            config=style_config,
        )
        identities = EligibilityResolver._style_scope_identities(
            session, task_id=task.id, config=style_config
        )
        config_row = session.scalar(select(TaskConfig).where(TaskConfig.task_id == task.id))

    assert identities["digest-included"] == (expected_hash, 1)
    assert preview.eligibility_digest
    assert config_row is not None
