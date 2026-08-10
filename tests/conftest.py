from __future__ import annotations

from collections.abc import Iterator

import pytest
from dataset_audit_studio.database.migrate import upgrade_database
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService


@pytest.fixture
def database(tmp_path) -> Iterator[Database]:
    instance = Database(tmp_path / "app.db", enforce_project_boundary=False)
    upgrade_database(instance)
    try:
        yield instance
    finally:
        instance.dispose()


@pytest.fixture
def task_service(database: Database) -> TaskService:
    return TaskService(database)
