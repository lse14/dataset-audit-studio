from __future__ import annotations

import base64
import binascii
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal

DirectoryPurpose = Literal["source", "output"]
FilePurpose = Literal["model"]
DialogMode = Literal["Directory", "File"]

_DIALOG_LOCK = threading.Lock()
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


def select_directory(
    *,
    project_root: Path,
    purpose: DirectoryPurpose,
    initial_path: str | None = None,
) -> str | None:
    return _select(
        project_root=project_root,
        purpose=purpose,
        descriptions=_DESCRIPTIONS,
        initial_path=initial_path,
        mode="Directory",
    )


def select_file(
    *,
    project_root: Path,
    purpose: FilePurpose,
    initial_path: str | None = None,
) -> str | None:
    return _select(
        project_root=project_root,
        purpose=purpose,
        descriptions=_FILE_DESCRIPTIONS,
        initial_path=initial_path,
        mode="File",
    )


def _select(
    *,
    project_root: Path,
    purpose: str,
    descriptions: dict[str, str],
    initial_path: str | None,
    mode: DialogMode,
) -> str | None:
    if sys.platform != "win32":
        raise DirectoryDialogUnavailable(
            "Windows directory selection is unavailable on this system."
        )
    if not _DIALOG_LOCK.acquire(blocking=False):
        raise DirectoryDialogBusy("Another directory selection window is already open.")

    try:
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
    initial_directory = (
        _existing_directory(initial_path)
        if mode == "Directory"
        else _existing_file_parent(initial_path)
    )
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
