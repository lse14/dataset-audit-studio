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
