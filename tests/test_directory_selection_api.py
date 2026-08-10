from __future__ import annotations

from pathlib import Path

import pytest
from dataset_audit_studio.api import workspace as workspace_api
from dataset_audit_studio.main import create_app
from dataset_audit_studio.workspace.windows_dialog import (
    DirectoryDialogBusy,
    DirectoryDialogError,
    DirectoryDialogUnavailable,
)
from fastapi.testclient import TestClient


def _app(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    return create_app(
        database_path=tmp_path / "directory-selection.db",
        enforce_runtime=False,
        project_root=project,
    )


def test_directory_selection_api_returns_selection_and_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path)
    selected = tmp_path / "selected"
    selected.mkdir()
    results = iter((str(selected), None))
    calls: list[tuple[Path, str, str | None]] = []

    def select_directory(*, project_root: Path, purpose: str, initial_path: str | None):
        calls.append((project_root, purpose, initial_path))
        return next(results)

    monkeypatch.setattr(workspace_api, "select_windows_directory", select_directory)

    with TestClient(app) as client:
        selection = client.post(
            "/api/filesystem/select-directory",
            json={"purpose": "source", "initial_path": str(tmp_path)},
        )
        cancellation = client.post(
            "/api/filesystem/select-directory",
            json={"purpose": "output", "initial_path": None},
        )

    assert selection.status_code == 200
    assert selection.json() == {"path": str(selected), "cancelled": False}
    assert cancellation.status_code == 200
    assert cancellation.json() == {"path": None, "cancelled": True}
    assert calls == [
        (tmp_path / "project", "source", str(tmp_path)),
        (tmp_path / "project", "output", None),
    ]


def test_file_selection_api_returns_selected_model_file_and_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path)
    selected = tmp_path / "replacement.safetensors"
    selected.write_bytes(b"model")
    results = iter((str(selected), None))
    calls: list[tuple[Path, str, str | None]] = []

    def select_file(*, project_root: Path, purpose: str, initial_path: str | None):
        calls.append((project_root, purpose, initial_path))
        return next(results)

    monkeypatch.setattr(workspace_api, "select_windows_file", select_file)

    with TestClient(app) as client:
        selection = client.post(
            "/api/filesystem/select-file",
            json={"purpose": "model", "initial_path": str(selected)},
        )
        cancellation = client.post(
            "/api/filesystem/select-file",
            json={"purpose": "model", "initial_path": None},
        )

    assert selection.status_code == 200
    assert selection.json() == {"path": str(selected), "cancelled": False}
    assert cancellation.status_code == 200
    assert cancellation.json() == {"path": None, "cancelled": True}
    assert calls == [
        (tmp_path / "project", "model", str(selected)),
        (tmp_path / "project", "model", None),
    ]


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (DirectoryDialogBusy("busy"), 409),
        (DirectoryDialogUnavailable("unavailable"), 501),
        (DirectoryDialogError("failed"), 500),
    ],
)
def test_directory_selection_api_maps_dialog_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: DirectoryDialogError,
    status_code: int,
) -> None:
    app = _app(tmp_path)

    def select_directory(**_: object) -> None:
        raise error

    monkeypatch.setattr(workspace_api, "select_windows_directory", select_directory)

    with TestClient(app) as client:
        response = client.post(
            "/api/filesystem/select-directory",
            json={"purpose": "source", "initial_path": None},
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == str(error)


def test_directory_selection_api_rejects_unknown_purpose(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/filesystem/select-directory",
            json={"purpose": "other", "initial_path": None},
        )
    assert response.status_code == 422
