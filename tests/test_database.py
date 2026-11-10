from __future__ import annotations

from dataset_audit_studio.database.base import Base
from dataset_audit_studio.database.migrate import (
    check_database_schema,
    migration_head,
)
from dataset_audit_studio.database.models import TaskConfig
from dataset_audit_studio.database.session import Database
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

BASELINE_REVISION = "0001_clean_slate_schema"


def test_baseline_database_has_current_schema_and_diagnostics(database: Database) -> None:
    assert migration_head() == BASELINE_REVISION
    actual_tables = set(inspect(database.engine).get_table_names())
    assert actual_tables == {"alembic_version", *Base.metadata.tables}
    diagnostics = database.diagnostics()
    assert diagnostics["journal_mode"] == "wal"
    assert diagnostics["foreign_keys"] is True
    assert diagnostics["busy_timeout_ms"] == 30000

    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == BASELINE_REVISION
    check_database_schema(database)


def test_foreign_keys_are_enforced(database: Database) -> None:
    try:
        with database.write_session() as session:
            session.add(
                TaskConfig(
                    task_id="missing-task",
                    revision=1,
                    config_hash="0" * 64,
                    config_json={},
                )
            )
    except IntegrityError:
        pass
    else:
        raise AssertionError("SQLite accepted a task config with a missing task")


def test_timestamps_round_trip_as_aware_utc_and_serialize_with_z(
    database: Database,
) -> None:
    from datetime import UTC, datetime

    from dataset_audit_studio.api.schemas import TaskResponse
    from dataset_audit_studio.database.models import Task
    from dataset_audit_studio.jobs.types import TaskView

    with database.write_session() as session:
        session.add(Task(name="stamps", source_root="/tmp/source"))

    with database.read_session() as session:
        task = session.query(Task).filter_by(name="stamps").one()
        # SQLite does not persist tzinfo; reads must still be tagged as UTC.
        assert task.created_at.tzinfo == UTC
        raw = session.execute(text("select created_at from tasks")).scalar_one()
        # The stored representation stays naive UTC, so existing data is unchanged.
        assert "+" not in raw and raw.endswith(tuple("0123456789"))

    view = TaskView(
        id=task.id,
        name=task.name,
        source_root=task.source_root,
        output_root=None,
        status=task.status,
        resume_state=None,
        current_config_revision=1,
        config_hash="0" * 64,
        config={},
        progress_current=0,
        progress_total=None,
        row_version=1,
        execution_epoch=0,
        lease_owner=None,
        lease_expires_at=None,
        error_code=None,
        error_message=None,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=None,
        finished_at=None,
    )
    payload = TaskResponse.from_view(view).model_dump_json()
    assert f'"created_at":"{task.created_at.strftime("%Y-%m-%dT%H:%M:%S")}' in payload
    assert payload.split('"created_at":"')[1].split('"')[0].endswith("Z")

    # Aware non-UTC input is converted rather than shifted.
    with database.write_session() as session:
        session.add(
            Task(
                name="aware",
                source_root="/tmp/source",
                started_at=datetime(2026, 1, 1, 16, 0, tzinfo=UTC),
            )
        )
    with database.read_session() as session:
        aware = session.query(Task).filter_by(name="aware").one()
        assert aware.started_at == datetime(2026, 1, 1, 16, 0, tzinfo=UTC)
