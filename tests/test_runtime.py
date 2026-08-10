from pathlib import Path

import pytest
from dataset_audit_studio.runtime import (
    PROJECT_ROOT,
    RuntimeIsolationError,
    assert_runtime_isolated,
    is_within_project,
    require_project_path,
)


def test_current_test_process_is_isolated() -> None:
    assert_runtime_isolated()


def test_project_path_boundary() -> None:
    assert is_within_project(PROJECT_ROOT / "data" / "tasks")
    assert not is_within_project(PROJECT_ROOT.parent / "outside")


def test_require_project_path_rejects_parent() -> None:
    with pytest.raises(RuntimeIsolationError):
        require_project_path(Path(PROJECT_ROOT.drive + "\\Windows"), "test path")
