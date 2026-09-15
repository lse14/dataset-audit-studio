from __future__ import annotations

import queue
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from dataset_audit_studio.jobs import phase_process
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from sqlalchemy import event
from sqlalchemy.exc import OperationalError


def _busy(code=sqlite3.SQLITE_BUSY):
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = code
    return OperationalError("BEGIN IMMEDIATE", {}, error)


def test_supervisor_does_not_kill_child_for_transient_checkpoint_lock(monkeypatch):
    now = datetime(2026, 9, 13, tzinfo=UTC)
    token = WorkerToken("task", "worker", 1)
    ticks = iter(range(0, 1000, 31))
    monkeypatch.setattr(phase_process.time, "monotonic", lambda: next(ticks))
    calls = []

    def heartbeat(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise _busy()

    current = SimpleNamespace(
        status="semantic_clustering", progress_current=0,
        lease_owner="worker", execution_epoch=1,
        lease_expires_at=now + timedelta(seconds=300),
    )
    tasks = SimpleNamespace(
        get_task=lambda _: current, latest_event_sequence=lambda _: 0,
        heartbeat=heartbeat, clock=lambda: now,
    )
    process = SimpleNamespace(
        start=lambda: None, is_alive=lambda: True, join=lambda **_: None,
        kill=lambda: None, terminate=lambda: None,
    )
    stopped = []
    process.is_alive = lambda: not stopped
    process.terminate = lambda: stopped.append(True)
    process.kill = lambda: stopped.append(True)

    def get(**_kwargs):
        if len(calls) < 2:
            raise queue.Empty
        stopped.append(False)
        return {"ok": True, "summary": {"component_complete": True}}

    result_queue = SimpleNamespace(get=get, close=lambda: None, join_thread=lambda: None)
    context = SimpleNamespace(Queue=lambda **_: result_queue, Process=lambda **_: process)
    monkeypatch.setattr(phase_process.multiprocessing, "get_context", lambda _: context)
    result = phase_process.run_isolated_phase_subprocess(
        SimpleNamespace(path="test.db"), tasks, token, None,
        phase_name="hierarchy", entrypoint=lambda *_: None,
    )
    assert result == {"component_complete": True}
    assert stopped == [False]
    assert len(calls) == 2


@pytest.mark.parametrize("mode", ["disk_error", "expired", "replaced"])
def test_heartbeat_does_not_hide_real_failure(mode):
    now = datetime(2026, 9, 13, tzinfo=UTC)
    token = WorkerToken("task", "worker", 1)

    def heartbeat(*_args, **_kwargs):
        raise _busy(sqlite3.SQLITE_IOERR if mode == "disk_error" else sqlite3.SQLITE_BUSY)

    tasks = SimpleNamespace(
        heartbeat=heartbeat, clock=lambda: now,
        get_task=lambda _: SimpleNamespace(
            lease_owner="worker", execution_epoch=2 if mode == "replaced" else 1,
            lease_expires_at=now + timedelta(seconds=-1 if mode == "expired" else 300),
        ),
    )
    with pytest.raises(OperationalError):
        phase_process._heartbeat_during_child_write(tasks, token)


def test_slow_atomic_batch_renews_from_end(database, task_service):
    from test_task_service import create_queued

    now = [datetime(2026, 9, 13, tzinfo=UTC)]
    tasks = TaskService(database, clock=lambda: now[0])
    task = create_queued(tasks)
    claimed = tasks.claim_next(owner="writer", lease_seconds=300)

    def slow_writer(_session):
        now[0] += timedelta(seconds=310)

    tasks.commit_batch(
        claimed.token, phase="scanning", config_hash=task.config_hash,
        batch_index=0, completed_items=0, cursor={}, batch_writer=slow_writer,
        lease_seconds=300,
    )
    # The atomic transaction excluded other workers, so its committed lease is fresh.
    tasks.heartbeat(claimed.token, lease_seconds=300)
    assert tasks.get_task(task.id).lease_expires_at.replace(tzinfo=UTC) == (
        now[0] + timedelta(seconds=300)
    )


def test_real_sqlite_writer_contention_allows_later_heartbeat(database, task_service):
    from test_task_service import create_queued

    create_queued(task_service)
    claimed = task_service.claim_next(owner="writer", lease_seconds=300)

    def short_busy_timeout(connection, _record):
        connection.execute("PRAGMA busy_timeout=1")

    event.listen(database.engine, "connect", short_busy_timeout)
    try:
        with database.write_session():
            phase_process._heartbeat_during_child_write(task_service, claimed.token)
        phase_process._heartbeat_during_child_write(task_service, claimed.token)
    finally:
        event.remove(database.engine, "connect", short_busy_timeout)
    assert task_service.get_task(claimed.task.id).lease_owner == "writer"


def test_leaf_similarity_calculation_runs_outside_write_transaction(
    database, task_service, tmp_path, monkeypatch,
):
    from dataset_audit_studio.clustering import repository
    from test_modular_clustering import (
        test_hierarchy_persists_leaf_scoped_semantic_duplicate_evidence as run_scenario,
    )

    writing = [False]
    calculations = []
    original = repository.semantic_duplicate_groups

    def trace(_connection, _cursor, statement, _params, _context, _many):
        if statement == "BEGIN IMMEDIATE":
            writing[0] = True

    def end(_connection):
        writing[0] = False

    def calculate(*args, **kwargs):
        assert not writing[0], "leaf similarity calculation holds the database writer"
        calculations.append(True)
        return original(*args, **kwargs)

    event.listen(database.engine, "before_cursor_execute", trace)
    event.listen(database.engine, "commit", end)
    event.listen(database.engine, "rollback", end)
    monkeypatch.setattr(repository, "semantic_duplicate_groups", calculate)
    try:
        run_scenario(database, task_service, tmp_path, monkeypatch)
    finally:
        event.remove(database.engine, "before_cursor_execute", trace)
        event.remove(database.engine, "commit", end)
        event.remove(database.engine, "rollback", end)
    assert calculations
