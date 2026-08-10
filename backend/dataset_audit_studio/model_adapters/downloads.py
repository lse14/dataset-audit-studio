from __future__ import annotations

import os
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dataset_audit_studio.model_adapters.errors import (
    ModelDownloadCanceled,
    ModelIntegrityError,
    ModelOperationConflict,
    ModelRegistryError,
)
from dataset_audit_studio.model_adapters.registry import DEFAULT_REGISTRY, ModelRegistry
from dataset_audit_studio.model_adapters.storage import ModelStorage
from dataset_audit_studio.model_adapters.types import (
    InstalledFile,
    ModelSpec,
    OperationSnapshot,
    RegistryFile,
)
from dataset_audit_studio.model_adapters.validation import validate_expected_file

OpenUrl = Callable[[urllib.request.Request, float], Any]
RANGE_PATTERN = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$")
TRUSTED_REDIRECT_SUFFIXES = (
    ".hf.co",
    ".huggingface.co",
    ".xethub.hf.co",
)
TRUSTED_REDIRECT_HOSTS = frozenset(
    {
        "download.pytorch.org",
        "huggingface.co",
        "openaipublic.azureedge.net",
        "raw.githubusercontent.com",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _default_open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - registry URLs are pinned


@dataclass
class _Operation:
    model_id: str
    operation: str
    status: str
    bytes_downloaded: int
    bytes_verified: int
    total_bytes: int
    current_file: str | None
    cancel_requested: bool
    error: str | None
    started_at: str
    updated_at: str
    cancel: threading.Event
    thread: threading.Thread | None = None


class ModelDownloadManager:
    def __init__(
        self,
        storage: ModelStorage,
        registry: ModelRegistry = DEFAULT_REGISTRY,
        *,
        open_url: OpenUrl = _default_open,
        request_timeout: float = 30.0,
    ) -> None:
        self.storage = storage
        self.registry = registry
        self.open_url = open_url
        self.request_timeout = request_timeout
        self._lock = threading.RLock()
        self._single_download = threading.Semaphore(1)
        self._operations: dict[str, _Operation] = {}
        self._shutdown = False

    def start_download(
        self,
        model_id: str,
        *,
        include_dependencies: bool = True,
    ) -> tuple[OperationSnapshot, ...]:
        models = (
            self.registry.dependency_order(model_id)
            if include_dependencies
            else (self.registry.get(model_id),)
        )
        return tuple(self._start(model, operation="download") for model in models)

    def start_download_all(self) -> tuple[OperationSnapshot, ...]:
        return tuple(self._start(model, operation="download") for model in self.registry.all())

    def start_verify(self, model_id: str) -> OperationSnapshot:
        return self._start(self.registry.get(model_id), operation="verify")

    def cancel(self, model_id: str) -> OperationSnapshot:
        with self._lock:
            operation = self._operations.get(model_id)
            if operation is None or operation.thread is None or not operation.thread.is_alive():
                raise ModelOperationConflict(f"Model has no active operation: {model_id}")
            operation.cancel_requested = True
            operation.cancel.set()
            operation.updated_at = _now()
            return self._snapshot(operation)

    def snapshot(self, model_id: str) -> OperationSnapshot | None:
        with self._lock:
            operation = self._operations.get(model_id)
            return self._snapshot(operation) if operation is not None else None

    def snapshots(self) -> dict[str, OperationSnapshot]:
        with self._lock:
            return {model_id: self._snapshot(value) for model_id, value in self._operations.items()}

    def shutdown(self, *, timeout: float = 30.0) -> bool:
        with self._lock:
            self._shutdown = True
            operations = tuple(self._operations.values())
            for operation in operations:
                operation.cancel_requested = True
                operation.cancel.set()
                operation.updated_at = _now()
        deadline = datetime.now(UTC).timestamp() + max(0.0, timeout)
        for operation in operations:
            thread = operation.thread
            if thread is None:
                continue
            remaining = max(0.0, deadline - datetime.now(UTC).timestamp())
            thread.join(remaining)
        return all(
            operation.thread is None or not operation.thread.is_alive()
            for operation in operations
        )

    def active_count(self) -> int:
        with self._lock:
            return sum(
                operation.thread is not None and operation.thread.is_alive()
                for operation in self._operations.values()
            )

    def _start(self, model: ModelSpec, *, operation: str) -> OperationSnapshot:
        with self._lock:
            if self._shutdown:
                raise ModelOperationConflict("Model download manager is shutting down")
            existing = self._operations.get(model.id)
            if existing is not None and existing.thread is not None and existing.thread.is_alive():
                if existing.operation != operation:
                    raise ModelOperationConflict(
                        f"Model {model.id} is already running {existing.operation}"
                    )
                return self._snapshot(existing)

            disk = self.storage.status(model)
            now = _now()
            state = _Operation(
                model_id=model.id,
                operation=operation,
                status="queued",
                bytes_downloaded=disk.bytes_downloaded,
                bytes_verified=0,
                total_bytes=model.total_size,
                current_file=None,
                cancel_requested=False,
                error=None,
                started_at=now,
                updated_at=now,
                cancel=threading.Event(),
            )
            if operation == "download" and disk.installation_status == "ready":
                state.status = "ready"
                state.bytes_verified = model.total_size
                self._operations[model.id] = state
                return self._snapshot(state)
            thread = threading.Thread(
                target=self._run,
                args=(model, state),
                name=f"model-{operation}-{model.id}",
                daemon=True,
            )
            state.thread = thread
            self._operations[model.id] = state
            thread.start()
            return self._snapshot(state)

    def _run(self, model: ModelSpec, operation: _Operation) -> None:
        try:
            with self._single_download:
                self._check_canceled(operation)
                if operation.operation == "download":
                    self._run_download(model, operation)
                else:
                    self._run_verify(model, operation)
                self._update(
                    operation,
                    status="ready",
                    bytes_downloaded=model.total_size,
                    bytes_verified=model.total_size,
                    current_file=None,
                    error=None,
                )
        except ModelDownloadCanceled:
            self._update(operation, status="canceled", current_file=None)
        except Exception as error:  # noqa: BLE001 - operation state must retain all failures
            self._update(
                operation,
                status="failed",
                current_file=None,
                error=f"{type(error).__name__}: {error}",
            )

    def _run_download(self, model: ModelSpec, operation: _Operation) -> None:
        installed: list[InstalledFile] = []
        downloaded_before = 0
        verified_before = 0
        for expected in model.files:
            self._check_canceled(operation)
            self._update(operation, status="downloading", current_file=expected.path)
            file_record = self._download_file(
                model,
                expected,
                operation,
                downloaded_before=downloaded_before,
                verified_before=verified_before,
            )
            installed.append(file_record)
            downloaded_before += expected.size
            verified_before += expected.size
            self._update(
                operation,
                bytes_downloaded=downloaded_before,
                bytes_verified=verified_before,
            )
        self._check_canceled(operation)
        self.storage.write_registry_manifest(model, tuple(installed))

    def _run_verify(self, model: ModelSpec, operation: _Operation) -> None:
        self._update(operation, status="verifying", bytes_verified=0)

        def progress(file_path: str, verified: int) -> None:
            self._check_canceled(operation)
            self._update(
                operation,
                status="verifying",
                current_file=file_path,
                bytes_verified=verified,
            )

        self.storage.verify_registry_model(model, progress=progress)

    def _download_file(
        self,
        model: ModelSpec,
        expected: RegistryFile,
        operation: _Operation,
        *,
        downloaded_before: int,
        verified_before: int,
    ) -> InstalledFile:
        final = self.storage.final_path(model, expected)
        part = self.storage.part_path(model, expected)
        final.parent.mkdir(parents=True, exist_ok=True)

        if final.exists():
            try:
                self.storage.require_safe_file(final, expected.path)
                self._verify_downloaded(
                    final,
                    expected,
                    operation,
                    verified_before=verified_before,
                )
                stat_result = final.stat()
                return InstalledFile(
                    path=expected.path,
                    size=expected.size,
                    sha256=expected.sha256,
                    mtime_ns=stat_result.st_mtime_ns,
                )
            except (ModelRegistryError, OSError):
                self.storage.quarantine(final, model_id=model.id)

        offset = 0
        if part.exists():
            try:
                self.storage.require_safe_file(part, expected.path)
            except ModelRegistryError:
                self.storage.quarantine(part, model_id=model.id)
            else:
                offset = part.stat().st_size
                if offset > expected.size:
                    self.storage.quarantine(part, model_id=model.id)
                    offset = 0
                elif offset == expected.size:
                    try:
                        self._verify_downloaded(
                            part,
                            expected,
                            operation,
                            verified_before=verified_before,
                        )
                    except ModelRegistryError:
                        self.storage.quarantine(part, model_id=model.id)
                        offset = 0
                    else:
                        os.replace(part, final)
                        stat_result = final.stat()
                        return InstalledFile(
                            path=expected.path,
                            size=expected.size,
                            sha256=expected.sha256,
                            mtime_ns=stat_result.st_mtime_ns,
                        )

        self._update(operation, bytes_downloaded=downloaded_before + offset)
        headers = {"Accept-Encoding": "identity", "User-Agent": "DatasetAuditStudio/0.1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(self.registry.file_url(model, expected), headers=headers)
        try:
            response_context = self.open_url(request, self.request_timeout)
        except urllib.error.HTTPError as error:
            raise ModelIntegrityError(
                f"HTTP {error.code} while downloading {model.id}/{expected.path}"
            ) from error

        with response_context as response:
            self._validate_response_url(response.geturl())
            raw_status = getattr(response, "status", None)
            status = int(raw_status if raw_status is not None else response.getcode())
            mode = "ab" if offset else "wb"
            if offset and status == 206:
                content_range = response.headers.get("Content-Range", "")
                match = RANGE_PATTERN.fullmatch(content_range)
                if (
                    match is None
                    or int(match.group(1)) != offset
                    or int(match.group(3)) != expected.size
                ):
                    raise ModelIntegrityError(
                        f"Invalid resume Content-Range for {model.id}/{expected.path}"
                    )
            elif offset and status == 200:
                offset = 0
                mode = "wb"
            elif status not in {200, 206}:
                raise ModelIntegrityError(
                    f"Unexpected HTTP {status} for {model.id}/{expected.path}"
                )

            written = offset
            oversized = False
            with part.open(mode) as stream:
                while True:
                    self._check_canceled(operation)
                    chunk = response.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    written += len(chunk)
                    if written > expected.size:
                        stream.flush()
                        os.fsync(stream.fileno())
                        oversized = True
                        break
                    self._update(
                        operation,
                        bytes_downloaded=downloaded_before + written,
                    )
                stream.flush()
                os.fsync(stream.fileno())

            if oversized:
                self.storage.quarantine(part, model_id=model.id)
                raise ModelIntegrityError(
                    f"Download exceeded registered size for {model.id}/{expected.path}"
                )

        self._check_canceled(operation)
        if written != expected.size:
            raise ModelIntegrityError(
                f"Incomplete download for {model.id}/{expected.path}: "
                f"expected {expected.size}, got {written}"
            )
        self._verify_downloaded(
            part,
            expected,
            operation,
            verified_before=verified_before,
        )
        os.replace(part, final)
        stat_result = final.stat()
        return InstalledFile(
            path=expected.path,
            size=expected.size,
            sha256=expected.sha256,
            mtime_ns=stat_result.st_mtime_ns,
        )

    def _verify_downloaded(
        self,
        path: Path,
        expected: RegistryFile,
        operation: _Operation,
        *,
        verified_before: int,
    ) -> None:
        self._update(operation, status="verifying", current_file=expected.path)

        def progress(current: int) -> None:
            self._check_canceled(operation)
            self._update(operation, bytes_verified=verified_before + current)

        try:
            validate_expected_file(path, expected, progress=progress)
        except ModelRegistryError:
            if path.exists() and path.name.endswith(".part"):
                self.storage.quarantine(path, model_id=operation.model_id)
            raise

    @staticmethod
    def _validate_response_url(url: str) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or (
                host not in TRUSTED_REDIRECT_HOSTS
                and not any(host.endswith(suffix) for suffix in TRUSTED_REDIRECT_SUFFIXES)
            )
        ):
            raise ModelIntegrityError(f"Model download redirected to an untrusted host: {host}")

    @staticmethod
    def _check_canceled(operation: _Operation) -> None:
        if operation.cancel.is_set():
            raise ModelDownloadCanceled(f"Model operation canceled: {operation.model_id}")

    def _update(self, operation: _Operation, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(operation, key, value)
            operation.updated_at = _now()

    @staticmethod
    def _snapshot(operation: _Operation) -> OperationSnapshot:
        return OperationSnapshot(
            model_id=operation.model_id,
            operation=operation.operation,  # type: ignore[arg-type]
            status=operation.status,  # type: ignore[arg-type]
            bytes_downloaded=operation.bytes_downloaded,
            bytes_verified=operation.bytes_verified,
            total_bytes=operation.total_bytes,
            current_file=operation.current_file,
            cancel_requested=operation.cancel_requested,
            error=operation.error,
            started_at=operation.started_at,
            updated_at=operation.updated_at,
        )
