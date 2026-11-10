from pathlib import Path

import pytest
from dataset_audit_studio.runtime import (
    PROJECT_ROOT,
    RuntimeIsolationError,
    assert_runtime_isolated,
    is_within_project,
    require_project_path,
)


@pytest.mark.requires_windows
def test_current_test_process_is_isolated() -> None:
    assert_runtime_isolated()


def test_project_path_boundary() -> None:
    assert is_within_project(PROJECT_ROOT / "data" / "tasks")
    assert not is_within_project(PROJECT_ROOT.parent / "outside")


def test_require_project_path_rejects_parent() -> None:
    outside = (
        Path(PROJECT_ROOT.drive + "\\Windows")
        if PROJECT_ROOT.drive
        else PROJECT_ROOT.parent / "Windows"
    )
    with pytest.raises(RuntimeIsolationError):
        require_project_path(outside, "test path")
