from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest
from dataset_audit_studio.workspace import windows_dialog


def _project_with_dialog_script(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "select_directory.ps1").write_text("# test placeholder\n", encoding="ascii")
    return project


def test_select_directory_decodes_unicode_path_and_uses_existing_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    selected = tmp_path / "\u6570\u636e\u96c6"
    selected.mkdir()
    captured: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        payload = base64.b64encode(str(selected).encode()).decode("ascii")
        return subprocess.CompletedProcess(command, 0, f"SELECTED:{payload}\n".encode(), b"")

    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(windows_dialog.subprocess, "run", run)

    result = windows_dialog.select_directory(
        project_root=project,
        purpose="source",
        initial_path=str(selected / "not-created" / "child"),
    )

    assert result == str(selected.resolve())
    command = captured["command"]
    assert isinstance(command, list)
    assert "-STA" in command
    assert command[command.index("-InitialPath") + 1] == str(selected.resolve())
    description = command[command.index("-Description") + 1]
    assert description == "\u9009\u62e9\u6e90\u6570\u636e\u76ee\u5f55"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["check"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_select_directory_returns_none_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        windows_dialog.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 0, b"CANCELLED\n", b""),
    )

    assert windows_dialog.select_directory(project_root=project, purpose="output") is None


def test_select_file_decodes_a_model_file_and_uses_the_native_file_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    selected = tmp_path / "replacement.safetensors"
    selected.write_bytes(b"model")
    captured: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        payload = base64.b64encode(str(selected).encode()).decode("ascii")
        return subprocess.CompletedProcess(command, 0, f"SELECTED:{payload}\n".encode(), b"")

    monkeypatch.setattr(windows_dialog.sys, "platform", "win32")
    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(windows_dialog.subprocess, "run", run)

    result = windows_dialog.select_file(
        project_root=project,
        purpose="model",
        initial_path=str(selected),
    )

    assert result == str(selected.resolve())
    command = captured["command"]
    assert isinstance(command, list)
    assert command[command.index("-Mode") + 1] == "File"
    assert command[command.index("-InitialPath") + 1] == str(selected.parent.resolve())
    assert command[command.index("-Description") + 1] == "选择本地模型文件"


def test_select_directory_rejects_nonexistent_selected_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    payload = base64.b64encode(str(tmp_path / "missing").encode()).decode("ascii")
    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        windows_dialog.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(
            command,
            0,
            f"SELECTED:{payload}\n".encode(),
            b"",
        ),
    )

    with pytest.raises(windows_dialog.DirectoryDialogError, match="existing absolute"):
        windows_dialog.select_directory(project_root=project, purpose="source")


def test_select_directory_reports_powershell_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        windows_dialog.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 3, b"", b"dialog failed"),
    )

    with pytest.raises(windows_dialog.DirectoryDialogError, match="exit code 3: dialog failed"):
        windows_dialog.select_directory(project_root=project, purpose="source")


def test_select_directory_rejects_a_second_open_dialog(tmp_path: Path) -> None:
    project = _project_with_dialog_script(tmp_path)
    assert windows_dialog._DIALOG_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(windows_dialog.DirectoryDialogBusy):
            windows_dialog.select_directory(project_root=project, purpose="source")
    finally:
        windows_dialog._DIALOG_LOCK.release()


def test_select_directory_is_windows_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(windows_dialog.sys, "platform", "linux")

    with pytest.raises(windows_dialog.DirectoryDialogUnavailable):
        windows_dialog.select_directory(project_root=tmp_path, purpose="source")


def test_picker_script_uses_the_windows_common_item_dialog() -> None:
    script = (
        Path(__file__).parents[1] / "scripts" / "select_directory.ps1"
    ).read_text(encoding="utf-8")

    assert "FolderBrowserDialog" not in script
    assert "OpenFileDialog" not in script
    assert "IFileDialog" in script
    assert "FileOpenDialog" in script
    assert "FOS_PICKFOLDERS" in script
    assert "SHCreateItemFromParsingName" in script
    assert "ComDialogFilterSpec" in script
    assert "int Show(IntPtr owner);" in script
    assert "$dialog.Dispose()" not in script
