from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
from pathlib import Path

import pytest
from dataset_audit_studio.model_adapters.downloads import ModelDownloadManager
from dataset_audit_studio.model_adapters.errors import ModelIntegrityError, ModelRegistryError
from dataset_audit_studio.model_adapters.registry import ModelRegistry
from dataset_audit_studio.model_adapters.service import ModelService
from dataset_audit_studio.model_adapters.storage import ModelStorage
from dataset_audit_studio.model_adapters.types import RegistryDocument


class FakeResponse:
    def __init__(
        self,
        data: bytes,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        max_chunk: int | None = None,
        delay: float = 0,
    ) -> None:
        self.stream = io.BytesIO(data)
        self.status = status
        self.headers = headers or {}
        self.max_chunk = max_chunk
        self.delay = delay

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.stream.close()

    def read(self, size: int) -> bytes:
        if self.delay:
            time.sleep(self.delay)
        return self.stream.read(min(size, self.max_chunk or size))

    def geturl(self) -> str:
        return "https://download.pytorch.org/models/test-model.json"

    def getcode(self) -> int:
        return self.status


class FakeOpener:
    def __init__(
        self,
        data: bytes,
        *,
        max_chunk: int | None = None,
        delay: float = 0,
        honor_range: bool = True,
    ) -> None:
        self.data = data
        self.max_chunk = max_chunk
        self.delay = delay
        self.honor_range = honor_range
        self.requests: list[str | None] = []
        self.opened = threading.Event()

    def __call__(self, request, _timeout: float) -> FakeResponse:
        raw_range = request.get_header("Range")
        self.requests.append(raw_range)
        self.opened.set()
        if raw_range and self.honor_range:
            offset = int(raw_range.removeprefix("bytes=").removesuffix("-"))
            return FakeResponse(
                self.data[offset:],
                status=206,
                headers={"Content-Range": f"bytes {offset}-{len(self.data) - 1}/{len(self.data)}"},
                max_chunk=self.max_chunk,
                delay=self.delay,
            )
        return FakeResponse(
            self.data,
            status=200,
            max_chunk=self.max_chunk,
            delay=self.delay,
        )


def _registry(data: bytes, *, sha256: str | None = None) -> ModelRegistry:
    payload = {
        "schema_version": 1,
        "registry_version": "2026-07-17.99",
        "evidence_sources": ["https://download.pytorch.org/models/test-model.json"],
        "models": [
            {
                "id": "test_model",
                "display_name": "Test model",
                "purpose": "test_asset",
                "source": {
                    "kind": "https",
                    "repository": None,
                    "revision": None,
                    "homepage": "https://download.pytorch.org/",
                    "license": "test",
                    "remote_code_allowed": False,
                },
                "files": [
                    {
                        "path": "model.json",
                        "size": len(data),
                        "sha256": sha256 or hashlib.sha256(data).hexdigest(),
                        "format": "json",
                        "url": "https://download.pytorch.org/models/test-model.json",
                    }
                ],
                "loader": "dinov2_style_guard_v1",
                "dependencies": [],
                "replaceable": False,
                "replacement_schema": None,
            }
        ],
    }
    document = RegistryDocument.model_validate(payload)
    return ModelRegistry(document, digest="d" * 64, source_path=Path(__file__))


def _wait(manager: ModelDownloadManager, model_id: str, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(model_id)
        if snapshot is not None and snapshot.status in {"canceled", "failed", "ready"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("model operation did not finish")


def test_download_resumes_verifies_and_atomically_installs(tmp_path: Path) -> None:
    data = json.dumps({"payload": "x" * 5000}).encode()
    registry = _registry(data)
    storage = ModelStorage(registry, models_root=tmp_path / "models")
    model = registry.get("test_model")
    part = storage.part_path(model, model.files[0])
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(data[:137])
    opener = FakeOpener(data)
    manager = ModelDownloadManager(storage, registry, open_url=opener)
    try:
        manager.start_download("test_model")
        result = _wait(manager, "test_model")
        assert result.status == "ready"
        assert opener.requests == ["bytes=137-"]
        assert storage.final_path(model, model.files[0]).read_bytes() == data
        assert not part.exists()
        assert storage.status(model).installation_status == "ready"
    finally:
        assert manager.shutdown()


def test_bad_hash_is_quarantined_and_never_becomes_ready(tmp_path: Path) -> None:
    data = json.dumps({"payload": "safe"}).encode()
    wrong_hash = hashlib.sha256(b"same-size-wrong").hexdigest()
    registry = _registry(data, sha256=wrong_hash)
    storage = ModelStorage(registry, models_root=tmp_path / "models")
    opener = FakeOpener(data)
    manager = ModelDownloadManager(storage, registry, open_url=opener)
    model = registry.get("test_model")
    try:
        manager.start_download("test_model")
        result = _wait(manager, "test_model")
        assert result.status == "failed"
        assert "SHA-256 mismatch" in (result.error or "")
        assert not storage.final_path(model, model.files[0]).exists()
        assert any(storage.quarantine_root.iterdir())
        assert storage.status(model).installation_status == "missing"
    finally:
        assert manager.shutdown()


def test_cancel_keeps_partial_file_for_a_later_resume(tmp_path: Path) -> None:
    data = json.dumps({"payload": "x" * 200_000}).encode()
    registry = _registry(data)
    storage = ModelStorage(registry, models_root=tmp_path / "models")
    opener = FakeOpener(data, max_chunk=256, delay=0.001)
    manager = ModelDownloadManager(storage, registry, open_url=opener)
    model = registry.get("test_model")
    part = storage.part_path(model, model.files[0])
    try:
        manager.start_download("test_model")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not part.exists() or part.stat().st_size == 0):
            time.sleep(0.005)
        canceled = manager.cancel("test_model")
        assert canceled.cancel_requested is True
        result = _wait(manager, "test_model")
        assert result.status == "canceled"
        assert part.is_file()
        assert 0 < part.stat().st_size < len(data)
        assert storage.status(model).installation_status == "partial"
    finally:
        assert manager.shutdown()

    partial_size = part.stat().st_size
    retry_opener = FakeOpener(data)
    retry = ModelDownloadManager(storage, registry, open_url=retry_opener)
    try:
        retry.start_download("test_model")
        resumed = _wait(retry, "test_model")
        assert resumed.status == "ready"
        assert retry_opener.requests == [f"bytes={partial_size}-"]
        assert storage.status(model).installation_status == "ready"
    finally:
        assert retry.shutdown()


def test_oversized_response_is_closed_then_quarantined(tmp_path: Path) -> None:
    data = json.dumps({"payload": "registered"}).encode()
    registry = _registry(data)
    storage = ModelStorage(registry, models_root=tmp_path / "models")
    opener = FakeOpener(data + b"unexpected")
    manager = ModelDownloadManager(storage, registry, open_url=opener)
    model = registry.get("test_model")
    try:
        manager.start_download("test_model")
        result = _wait(manager, "test_model")
        assert result.status == "failed"
        assert "exceeded registered size" in (result.error or "")
        assert not storage.final_path(model, model.files[0]).exists()
        assert not storage.part_path(model, model.files[0]).exists()
        assert any(storage.quarantine_root.iterdir())
    finally:
        assert manager.shutdown()


def test_first_use_starts_automatic_download(tmp_path: Path) -> None:
    data = json.dumps({"payload": "auto"}).encode()
    registry = _registry(data)
    storage = ModelStorage(registry, models_root=tmp_path / "models")
    manager = ModelDownloadManager(storage, registry, open_url=FakeOpener(data))
    service = ModelService(storage, manager, registry)
    try:
        with pytest.raises(ModelRegistryError, match="not runtime-ready"):
            service.require_ready("test_model")
        assert _wait(manager, "test_model").status == "ready"
        root = service.require_ready("test_model")
        assert root.is_dir()
        assert (root / "model.json").is_file()
    finally:
        assert service.shutdown()


def test_runtime_verification_invalidates_same_mtime_tampering(tmp_path: Path) -> None:
    data = json.dumps({"payload": "original"}).encode()
    registry = _registry(data)
    storage = ModelStorage(registry, models_root=tmp_path / "models")
    manager = ModelDownloadManager(storage, registry, open_url=FakeOpener(data))
    model = registry.get("test_model")
    manager.start_download("test_model")
    assert _wait(manager, "test_model").status == "ready"
    assert manager.shutdown()

    final = storage.final_path(model, model.files[0])
    original_stat = final.stat()
    tampered = data.replace(b"original", b"modified")
    assert len(tampered) == len(data)
    final.write_bytes(tampered)
    os.utime(final, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert storage.status(model).installation_status == "ready"

    verifier = ModelDownloadManager(storage, registry, open_url=FakeOpener(data))
    service = ModelService(storage, verifier, registry)
    try:
        with pytest.raises(ModelIntegrityError, match="SHA-256 mismatch"):
            service.require_ready("test_model")
        assert not final.exists()
        assert storage.status(model).installation_status == "missing"
        assert len(tuple(storage.quarantine_root.iterdir())) >= 2
    finally:
        assert service.shutdown()
