from __future__ import annotations

import sys
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


# Windows-only runtime binding. Marked tests spawn the real scoring/clustering/style
# subprocesses, whose production entry points call ``assert_runtime_isolated()``
# (project ``.venv`` + ``Scripts`` layout and a fixed Windows environment), or they
# shell out to ``powershell.exe``. The binding lives in production code, so it cannot
# be neutralised from a test without weakening it. The mark is applied here rather
# than imported from this module so collection works under any pytest invocation.
WINDOWS_ONLY_REASON = "Windows-only runtime binding"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", f"requires_windows: {WINDOWS_ONLY_REASON.lower()}"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform == "win32":
        return
    skip = pytest.mark.skip(reason=WINDOWS_ONLY_REASON)
    for item in items:
        if "requires_windows" in item.keywords:
            item.add_marker(skip)
