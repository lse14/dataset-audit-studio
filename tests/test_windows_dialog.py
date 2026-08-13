from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from dataset_audit_studio import main as app_main
from dataset_audit_studio.workspace import windows_dialog
from fastapi.testclient import TestClient


def _project_with_dialog_script(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "select_directory.ps1").write_text("# test placeholder\n", encoding="ascii")
    return project


class _FakePickerProcess:
    def __init__(self, stdout: str) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO()
        self.returncode: int | None = None
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 1
        self.terminated = True

    def kill(self) -> None:
        self.returncode = 1
        self.killed = True


class _HangingReadyProcess:
    def __init__(self) -> None:
        self._unblocked = threading.Event()
        self.stdin = io.StringIO()
        self.stdout = self
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def readline(self) -> str:
        self._unblocked.wait(timeout=60)
        return ""

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self._unblocked.set()
        if self.returncode is None:
            self.returncode = 1
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1
        self._unblocked.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = 1
        self._unblocked.set()


class _ExplodingReadyProcess:
    def __init__(self, error: OSError) -> None:
        self._error = error
        self.stdin = io.StringIO()
        self.stdout = self
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def readline(self) -> str:
        raise self._error

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 1
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1

    def kill(self) -> None:
        self.killed = True
        self.returncode = 1


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


def test_native_picker_host_reuses_one_sta_process_and_closes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    selected = tmp_path / "selected"
    selected.mkdir()
    payload = base64.b64encode(str(selected).encode()).decode("ascii")
    process = _FakePickerProcess(f"READY\nSELECTED:{payload}\nCANCELLED\n")
    commands: list[list[str]] = []

    def popen(command: list[str], **_: object) -> _FakePickerProcess:
        commands.append(command)
        return process

    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(windows_dialog.subprocess, "Popen", popen)

    host = windows_dialog.NativePickerHost(project)

    assert host.select(
        description="选择源数据目录",
        initial_path=str(selected),
        mode="Directory",
    ) == str(selected.resolve())
    assert host.select(
        description="选择输出目录",
        initial_path=None,
        mode="Directory",
    ) is None

    host.close()

    assert len(commands) == 1
    assert "-STA" in commands[0]
    assert "-Server" in commands[0]
    lines = process.stdin.getvalue().splitlines()
    assert lines[-1] == "QUIT"
    requests = [
        json.loads(base64.b64decode(line.removeprefix("REQUEST:")).decode("utf-8"))
        for line in lines[:-1]
    ]
    assert requests == [
        {
            "description": "选择源数据目录",
            "initial_path": str(selected),
            "mode": "Directory",
        },
        {
            "description": "选择输出目录",
            "initial_path": None,
            "mode": "Directory",
        },
    ]
    assert process.wait_timeouts == [2]


def test_native_picker_host_does_not_pipe_unread_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    process = _FakePickerProcess("READY\n")
    captured: dict[str, object] = {}

    def popen(command: list[str], **kwargs: object) -> _FakePickerProcess:
        captured.update(kwargs)
        return process

    monkeypatch.setattr(windows_dialog.sys, "platform", "win32")
    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(windows_dialog.subprocess, "Popen", popen)

    host = windows_dialog.NativePickerHost(project)
    host.start()
    try:
        assert captured["stderr"] is subprocess.DEVNULL
    finally:
        host.close()


def test_start_ready_timeout_terminates_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    process = _HangingReadyProcess()
    monkeypatch.setattr(windows_dialog, "_READY_TIMEOUT_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(windows_dialog.sys, "platform", "win32")
    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(windows_dialog.subprocess, "Popen", lambda command, **_: process)

    host = windows_dialog.NativePickerHost(project)
    error: list[BaseException] = []

    def run_start() -> None:
        try:
            host.start()
        except BaseException as exc:
            error.append(exc)

    worker = threading.Thread(target=run_start)
    worker.start()
    try:
        worker.join(timeout=2.0)
        assert not worker.is_alive()
        assert error
        assert isinstance(error[0], windows_dialog.DirectoryDialogError)
        assert process.poll() is not None
        assert process.terminated or process.killed
        assert host._process is None
    finally:
        process.terminate()
        worker.join(timeout=1.0)


def test_start_failure_after_popen_terminates_unassigned_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    process = _ExplodingReadyProcess(OSError("pipe closed"))
    monkeypatch.setattr(windows_dialog.sys, "platform", "win32")
    monkeypatch.setattr(windows_dialog.shutil, "which", lambda _: "powershell.exe")
    monkeypatch.setattr(windows_dialog.subprocess, "Popen", lambda command, **_: process)

    host = windows_dialog.NativePickerHost(project)
    with pytest.raises(windows_dialog.DirectoryDialogError):
        host.start()

    assert process.poll() is not None
    assert process.terminated or process.killed
    assert host._process is None


def test_close_returns_when_another_thread_holds_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project_with_dialog_script(tmp_path)
    process = _FakePickerProcess("READY\n")
    monkeypatch.setattr(windows_dialog, "_CLOSE_LOCK_TIMEOUT_SECONDS", 0.3, raising=False)

    host = windows_dialog.NativePickerHost(project)
    host._process = process

    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with host._lock:
            held.set()
            release.wait(timeout=30)

    holder_thread = threading.Thread(target=holder, daemon=True)
    holder_thread.start()
    assert held.wait(timeout=2)

    done = threading.Event()
    close_error: list[BaseException] = []

    def do_close() -> None:
        try:
            host.close()
        except BaseException as exc:
            close_error.append(exc)
        finally:
            done.set()

    closer = threading.Thread(target=do_close)
    closer.start()
    try:
        finished = done.wait(timeout=2.0)
        assert finished
        assert not close_error
        assert process.poll() is not None
        assert host._process is None
    finally:
        release.set()
        closer.join(timeout=1.0)
        holder_thread.join(timeout=1.0)


def test_webui_prewarms_and_closes_native_picker_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePickerHost:
        instances: list[FakePickerHost] = []

        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root
            self.started = 0
            self.closed = 0
            self.instances.append(self)

        def start(self) -> None:
            self.started += 1

        def close(self) -> None:
            self.closed += 1

    monkeypatch.setattr(app_main, "NativePickerHost", FakePickerHost)
    app = app_main.create_app(
        database_path=tmp_path / "picker-lifecycle.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
        project_root=tmp_path,
        start_worker=False,
        prewarm_picker=True,
    )

    with TestClient(app):
        host = app.state.native_picker_host
        assert host is FakePickerHost.instances[0]
        assert host.project_root == tmp_path
        assert host.started == 1

    assert host.closed == 1


def test_webui_starts_when_native_picker_prewarm_raises_unexpected_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingPickerHost:
        instances: list[FailingPickerHost] = []

        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root
            self.closed = 0
            self.instances.append(self)

        def start(self) -> None:
            raise RuntimeError("picker prewarm failed")

        def close(self) -> None:
            self.closed += 1

    monkeypatch.setattr(app_main, "NativePickerHost", FailingPickerHost)
    app = app_main.create_app(
        database_path=tmp_path / "picker-prewarm-failure.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
        project_root=tmp_path,
        start_worker=False,
        prewarm_picker=True,
    )

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    assert FailingPickerHost.instances[0].closed == 1


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


def test_picker_script_server_announces_ready_and_stops_on_quit() -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    script = Path(__file__).parents[1] / "scripts" / "select_directory.ps1"

    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-STA",
            "-File",
            str(script),
            "-Server",
        ],
        input="QUIT\n",
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[0] == "READY"
