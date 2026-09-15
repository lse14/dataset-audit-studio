from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from dataset_audit_studio.app import duplicate_evidence
from dataset_audit_studio.app.component_catalog import build_component_registry
from dataset_audit_studio.app.component_execution import (
    ComponentExecutionDependencies,
    build_component_execution_catalog,
)
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.core.component_contracts import ComponentRunRequest
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import Evidence, Sample, Task, WorkerLease
from dataset_audit_studio.jobs.errors import StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from sqlalchemy import event, select


def _claimed_metrics(database, tmp_path):
    now = [datetime(2026, 9, 13, tzinfo=UTC)]
    tasks = TaskService(database, clock=lambda: now[0])
    task = tasks.create_task(
        name="lease regression",
        source_root=str(tmp_path),
        output_root=None,
        config=ComponentTaskConfigMaterializer().materialize(
            materialize_profile("general")["components"],
            profile="general", require_profile=True,
        ),
    )
    tasks.queue_task(task.id)
    scan = tasks.claim_next(owner="scan", lease_seconds=300)
    tasks.complete_phase(scan.token, phase=TaskStatus.SCANNING)
    claimed = tasks.claim_next(owner="metrics", lease_seconds=300)
    with database.write_session() as session:
        for i in range(2):
            session.add(Sample(
                id=f"sample-{i}", task_id=task.id, relative_path=f"{i}.png",
                source_size=1, source_mtime_ns=1, source_sha256="a" * 64,
                media_kind="image", artist_scope="__root__", scan_state="valid",
                phash="0" * 16, colorhash="0" * 16, scan_algorithm_version="test",
            ))
    return tasks, claimed, now


def _execute(tasks, claimed, tmp_path):
    catalog = build_component_execution_catalog(
        ComponentExecutionDependencies(
            database=tasks.database, tasks=tasks, model_service=None,
            project_root=tmp_path, subprocess=False,
        ),
        registry=build_component_registry(),
    )
    return catalog.execute(ComponentRunRequest(
        task_id=claimed.task.id, component_id="metrics.technical",
        worker_owner=claimed.token.owner, execution_epoch=claimed.token.execution_epoch,
        normalized_config={}, component_order=("metrics.technical",), runtime_model_ids=(),
    ))


def _evidence(database):
    with database.read_session() as session:
        return [(row.id, row.code, row.sample_id, row.value_json)
                for row in session.scalars(select(Evidence).order_by(Evidence.id))]


def test_metrics_survives_twenty_minutes_of_matching(database, tmp_path, monkeypatch):
    tasks, claimed, now = _claimed_metrics(database, tmp_path)
    original = duplicate_evidence.visual_duplicate_groups
    heartbeat_times = []
    monkeypatch.setattr(duplicate_evidence, "monotonic", lambda: now[0].timestamp(), raising=False)

    def slow_matching(*args, **kwargs):
        pulse = kwargs.get("heartbeat")
        for _ in range(40):
            now[0] += timedelta(seconds=30)
            if pulse is not None:
                pulse()
                with database.read_session() as session:
                    heartbeat_times.append(session.get(WorkerLease, 1).heartbeat_at)
        return original(*args, **kwargs)

    monkeypatch.setattr(duplicate_evidence, "visual_duplicate_groups", slow_matching)
    _execute(tasks, claimed, tmp_path)
    assert tasks.get_task(claimed.task.id).status == TaskStatus.QUEUED.value
    assert len(set(heartbeat_times)) == 40
    assert len(_evidence(database)) == 4
    checkpoint = tasks.list_checkpoints(claimed.task.id, phase="cpu_metrics")[0]
    assert checkpoint.completed_items == 2


def test_matching_does_not_hold_database_write_lock(database, tmp_path, monkeypatch):
    tasks, claimed, _ = _claimed_metrics(database, tmp_path)
    original = duplicate_evidence.visual_duplicate_groups
    writer_locks = []

    def trace(_connection, _cursor, statement, _params, _context, _many):
        if statement == "BEGIN IMMEDIATE":
            writer_locks.append(statement)

    def matching(*args, **kwargs):
        assert not writer_locks, "duplicate matching is inside a write transaction"
        return original(*args, **kwargs)

    event.listen(database.engine, "before_cursor_execute", trace)
    monkeypatch.setattr(duplicate_evidence, "visual_duplicate_groups", matching)
    try:
        # Tokenless maintenance path also must keep computation outside the writer lock.
        duplicate_evidence.DuplicateEvidenceProducer(database).produce(
            claimed.task.id, claimed.task.config_hash,
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", trace)
    assert len(_evidence(database)) == 4


@pytest.mark.parametrize("failure", ["expired", "replaced", "config_changed"])
def test_stale_matching_never_replaces_existing_evidence(
    database, tmp_path, monkeypatch, failure,
):
    tasks, claimed, now = _claimed_metrics(database, tmp_path)
    producer = duplicate_evidence.DuplicateEvidenceProducer(database)
    producer.produce(claimed.task.id, claimed.task.config_hash)
    before = _evidence(database)
    original = duplicate_evidence.visual_duplicate_groups

    def invalidate(*args, **kwargs):
        result = original(*args, **kwargs)
        if failure == "expired":
            now[0] += timedelta(seconds=301)
        else:
            with database.write_session() as session:
                task = session.get(Task, claimed.task.id)
                if failure == "replaced":
                    task.execution_epoch += 1
                else:
                    task.current_config_revision += 1
        return result

    monkeypatch.setattr(duplicate_evidence, "visual_duplicate_groups", invalidate)
    with pytest.raises((StaleWorkerToken, RuntimeError)):
        _execute(tasks, claimed, tmp_path)
    assert _evidence(database) == before


def test_evidence_insert_failure_rolls_back_replacement(database, tmp_path, monkeypatch):
    tasks, claimed, _ = _claimed_metrics(database, tmp_path)
    producer = duplicate_evidence.DuplicateEvidenceProducer(database)
    producer.produce(claimed.task.id, claimed.task.config_hash)
    before = _evidence(database)
    monkeypatch.setattr(duplicate_evidence, "_WRITE_BATCH_SIZE", 1, raising=False)
    inserts = []

    def fail_insert(_connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO evidence"):
            inserts.append(statement)
            if len(inserts) == 2:
                raise RuntimeError("injected insert failure")

    event.listen(database.engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="injected insert failure"):
            _execute(tasks, claimed, tmp_path)
    finally:
        event.remove(database.engine, "before_cursor_execute", fail_insert)
    assert _evidence(database) == before


def test_visual_matching_calls_heartbeat_and_preserves_groups():
    from dataset_audit_studio.clustering.dedupe import visual_duplicate_groups

    kwargs = dict(
        indices=tuple(range(20)), phashes=("0",) * 20, colorhashes=("0",) * 20,
        phash_max_distance=8, colorhash_max_distance=8, rank=lambda index: (index,),
    )
    expected = visual_duplicate_groups(**kwargs)
    calls = []
    actual = visual_duplicate_groups(**kwargs, heartbeat=lambda: calls.append(True))
    assert actual == expected
    assert len(calls) == 19


def test_bulk_writes_renew_inside_transaction(database, tmp_path, monkeypatch):
    tasks, claimed, now = _claimed_metrics(database, tmp_path)
    monkeypatch.setattr(duplicate_evidence, "monotonic", lambda: now[0].timestamp())
    monkeypatch.setattr(duplicate_evidence, "_WRITE_BATCH_SIZE", 1)

    def slow_insert(_connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO evidence"):
            now[0] += timedelta(seconds=100)

    event.listen(database.engine, "after_cursor_execute", slow_insert)
    try:
        _execute(tasks, claimed, tmp_path)
    finally:
        event.remove(database.engine, "after_cursor_execute", slow_insert)
    assert len(_evidence(database)) == 4
    assert tasks.get_task(claimed.task.id).status == TaskStatus.QUEUED.value
