from __future__ import annotations

import base64
import binascii
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, Literal

DirectoryPurpose = Literal["source", "output"]
FilePurpose = Literal["model"]
DialogMode = Literal["Directory", "File"]

_DIALOG_LOCK = threading.Lock()
_READY_TIMEOUT_SECONDS = 15.0
_CLOSE_LOCK_TIMEOUT_SECONDS = 2.0
_DESCRIPTIONS: dict[DirectoryPurpose, str] = {
    "source": "\u9009\u62e9\u6e90\u6570\u636e\u76ee\u5f55",
    "output": "\u9009\u62e9\u8f93\u51fa\u76ee\u5f55",
}
_FILE_DESCRIPTIONS: dict[FilePurpose, str] = {
    "model": "\u9009\u62e9\u672c\u5730\u6a21\u578b\u6587\u4ef6",
}


class DirectoryDialogError(RuntimeError):
    pass


class DirectoryDialogBusy(DirectoryDialogError):
    pass


class DirectoryDialogUnavailable(DirectoryDialogError):
    pass


class NativePickerHost:
    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve(strict=False)
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def select(
        self,
        *,
        description: str,
        initial_path: str | None,
        mode: DialogMode,
    ) -> str | None:
        if not description:
            raise DirectoryDialogError("Unsupported Windows path selection purpose")
        with self._lock:
            self._start_locked()
            process = self._process
            assert process is not None
            payload = base64.b64encode(
                json.dumps(
                    {
                        "description": description,
                        "initial_path": initial_path,
                        "mode": mode,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).decode("ascii")
            try:
                assert process.stdin is not None
                assert process.stdout is not None
                process.stdin.write(f"REQUEST:{payload}\n")
                process.stdin.flush()
                response = process.stdout.readline()
            except OSError as error:
                self._discard_locked()
                raise DirectoryDialogError("Windows directory selection process failed.") from error
            if not response:
                self._discard_locked()
                raise DirectoryDialogError(
                    "Windows directory selection process stopped unexpectedly."
                )
            return _decode_picker_response(response, mode=mode)

    def close(self) -> None:
        acquired = self._lock.acquire(timeout=_CLOSE_LOCK_TIMEOUT_SECONDS)
        if not acquired:
            # Shutdown must be able to break a select() blocked in the native dialog.
            process = self._process
            self._process = None
            if process is not None:
                self._terminate(process)
            return
        try:
            process = self._process
            self._process = None
            if process is None or process.poll() is not None:
                return
            try:
                assert process.stdin is not None
                process.stdin.write("QUIT\n")
                process.stdin.flush()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self._terminate(process)
        finally:
            self._lock.release()

    def _start_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._process = None
        if sys.platform != "win32":
            raise DirectoryDialogUnavailable(
                "Windows directory selection is unavailable on this system."
            )
        root, script = _dialog_script(self._project_root)
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            raise DirectoryDialogUnavailable("Windows PowerShell is unavailable.")
        command = [
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
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise DirectoryDialogError(
                "Windows PowerShell could not start the directory picker."
            ) from error
        self._process = process
        try:
            assert process.stdout is not None
            try:
                ready_line = _readline_with_timeout(process.stdout, _READY_TIMEOUT_SECONDS)
            except DirectoryDialogError:
                raise
            except Exception as error:
                raise DirectoryDialogError(
                    "Windows directory selection process did not become ready."
                ) from error
            if ready_line.strip() != "READY":
                raise DirectoryDialogError(
                    "Windows directory selection process did not become ready."
                )
        except BaseException:
            self._abandon_process(process)
            raise

    def _abandon_process(self, process: subprocess.Popen[str]) -> None:
        if self._process is process:
            self._process = None
        self._terminate(process)

    def _discard_locked(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            self._terminate(process)

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2)
            except OSError:
                pass


def _readline_with_timeout(stream: IO[str], timeout: float) -> str:
    holder: list[str | BaseException] = []
    done = threading.Event()

    def _read() -> None:
        try:
            holder.append(stream.readline())
        except BaseException as error:
            holder.append(error)
        finally:
            done.set()

    # readline has no portable timeout; process teardown eventually closes this daemon's pipe.
    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    if not done.wait(timeout=timeout):
        raise TimeoutError("Windows directory selection process did not become ready.")
    if not holder:
        raise DirectoryDialogError("Windows directory selection process did not become ready.")
    outcome = holder[0]
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def select_directory(
    *,
    project_root: Path,
    purpose: DirectoryPurpose,
    initial_path: str | None = None,
    picker_host: NativePickerHost | None = None,
) -> str | None:
    return _select(
        project_root=project_root,
        purpose=purpose,
        descriptions=_DESCRIPTIONS,
        initial_path=initial_path,
        mode="Directory",
        picker_host=picker_host,
    )


def select_file(
    *,
    project_root: Path,
    purpose: FilePurpose,
    initial_path: str | None = None,
    picker_host: NativePickerHost | None = None,
) -> str | None:
    return _select(
        project_root=project_root,
        purpose=purpose,
        descriptions=_FILE_DESCRIPTIONS,
        initial_path=initial_path,
        mode="File",
        picker_host=picker_host,
    )


def _select(
    *,
    project_root: Path,
    purpose: str,
    descriptions: dict[str, str],
    initial_path: str | None,
    mode: DialogMode,
    picker_host: NativePickerHost | None = None,
) -> str | None:
    if sys.platform != "win32":
        raise DirectoryDialogUnavailable(
            "Windows directory selection is unavailable on this system."
        )
    if not _DIALOG_LOCK.acquire(blocking=False):
        raise DirectoryDialogBusy("Another directory selection window is already open.")

    try:
        if picker_host is not None:
            return picker_host.select(
                description=descriptions.get(purpose, ""),
                initial_path=_initial_directory(initial_path, mode=mode),
                mode=mode,
            )
        return _run_dialog(
            project_root=project_root,
            description=descriptions.get(purpose, ""),
            initial_path=initial_path,
            mode=mode,
        )
    finally:
        _DIALOG_LOCK.release()


def _run_dialog(
    *,
    project_root: Path,
    description: str,
    initial_path: str | None,
    mode: DialogMode,
) -> str | None:
    root, script = _dialog_script(project_root)
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise DirectoryDialogUnavailable("Windows PowerShell is unavailable.")

    if not description:
        raise DirectoryDialogError("Unsupported Windows path selection purpose")

    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-STA",
        "-File",
        str(script),
        "-Description",
        description,
    ]
    if mode == "File":
        command.extend(("-Mode", mode))
    initial_directory = _initial_directory(initial_path, mode=mode)
    if initial_directory is not None:
        command.extend(("-InitialPath", initial_directory))

    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as error:
        raise DirectoryDialogError(
            "Windows PowerShell could not start the directory picker."
        ) from error

    if completed.returncode != 0:
        detail = _output_text(completed.stderr).strip()
        suffix = f": {detail[-500:]}" if detail else ""
        raise DirectoryDialogError(
            f"Windows directory selection failed with exit code {completed.returncode}{suffix}"
        )

    for line in reversed(_output_text(completed.stdout).splitlines()):
        marker = line.strip()
        if marker == "CANCELLED":
            return None
        if marker.startswith("SELECTED:"):
            return _decode_selected_path(
                marker.removeprefix("SELECTED:"),
                mode=mode,
            )
    raise DirectoryDialogError("Windows directory selection returned an invalid response.")


def _dialog_script(project_root: Path) -> tuple[Path, Path]:
    root = project_root.resolve(strict=False)
    script = (root / "scripts" / "select_directory.ps1").resolve(strict=False)
    try:
        script.relative_to(root)
    except ValueError as error:
        raise DirectoryDialogUnavailable(
            "The directory selection script is outside the project."
        ) from error
    if not script.is_file():
        raise DirectoryDialogUnavailable("The Windows directory selection script is missing.")
    return root, script


def _initial_directory(initial_path: str | None, *, mode: DialogMode) -> str | None:
    return (
        _existing_directory(initial_path)
        if mode == "Directory"
        else _existing_file_parent(initial_path)
    )


def _existing_directory(initial_path: str | None) -> str | None:
    if initial_path is None or not initial_path.strip():
        return None
    try:
        candidate = Path(initial_path.strip())
        if not candidate.is_absolute():
            return None
        for possible in (candidate, *candidate.parents):
            if possible.is_dir():
                return str(possible.resolve(strict=True))
    except OSError:
        return None
    return None


def _existing_file_parent(initial_path: str | None) -> str | None:
    if initial_path is None or not initial_path.strip():
        return None
    try:
        candidate = Path(initial_path.strip())
        if not candidate.is_absolute():
            return None
        starting = candidate if candidate.is_dir() else candidate.parent
        for possible in (starting, *starting.parents):
            if possible.is_dir():
                return str(possible.resolve(strict=True))
    except OSError:
        return None
    return None


def _decode_picker_response(response: str, *, mode: DialogMode) -> str | None:
    marker = response.strip()
    if marker == "CANCELLED":
        return None
    if marker.startswith("SELECTED:"):
        return _decode_selected_path(marker.removeprefix("SELECTED:"), mode=mode)
    if marker.startswith("ERROR:"):
        try:
            detail = base64.b64decode(
                marker.removeprefix("ERROR:"), validate=True
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            detail = "Windows directory selection process returned an error."
        raise DirectoryDialogError(
            detail or "Windows directory selection process returned an error."
        )
    raise DirectoryDialogError("Windows directory selection returned an invalid response.")


def _decode_selected_path(payload: str, *, mode: DialogMode) -> str:
    try:
        decoded = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as error:
        raise DirectoryDialogError(
            "Windows directory selection returned an invalid path."
        ) from error

    selected = Path(decoded)
    try:
        valid = selected.is_dir() if mode == "Directory" else selected.is_file()
        if not selected.is_absolute() or not valid:
            kind = "directory" if mode == "Directory" else "file"
            raise DirectoryDialogError(
                f"Windows {kind} selection did not return an existing absolute {kind}."
            )
        return str(selected.resolve(strict=True))
    except OSError as error:
        raise DirectoryDialogError("The selected directory is no longer available.") from error


def _output_text(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8-sig", errors="replace")
    return output
