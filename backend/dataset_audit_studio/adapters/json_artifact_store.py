from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataset_audit_studio.core.json_artifacts import JsonArtifact
from dataset_audit_studio.database.enums import ArtifactState
from dataset_audit_studio.database.models import Artifact
from dataset_audit_studio.runtime import PROJECT_ROOT

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SAFE_KIND = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RETRY_ATTEMPTS = 11
_RETRY_DELAY_SECONDS = 0.1
_RETRYABLE_WINERRORS = frozenset((32, 33))
_T = TypeVar("_T")


def _retry_windows_lock(operation: Callable[[], _T]) -> _T:
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return operation()
        except OSError as error:
            if getattr(error, "winerror", None) not in _RETRYABLE_WINERRORS:
                raise
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
    raise AssertionError("unreachable retry loop")


class JsonArtifactStore:
    def __init__(self, *, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve(strict=False)

    def write(
        self,
        *,
        task_id: str,
        producer_id: str,
        kind: str,
        cache_key: str,
        payload: dict[str, Any],
    ) -> JsonArtifact:
        directory = self._directory(task_id, producer_id)
        directory.mkdir(parents=True, exist_ok=True)
        self._validate(kind, cache_key)
        final = directory / f"{cache_key}.json"
        part = final.with_suffix(".json.part")
        content = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        failure: BaseException | None = None
        try:
            def write() -> None:
                with part.open("wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())

            _retry_windows_lock(write)
            _retry_windows_lock(lambda: os.replace(part, final))
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                _retry_windows_lock(lambda: part.unlink(missing_ok=True))
            except BaseException as cleanup_error:
                if failure is None:
                    raise
                failure.add_note(
                    f"Unable to remove temporary artifact file {part}: {cleanup_error}"
                )
        return self.inspect(
            task_id=task_id,
            producer_id=producer_id,
            kind=kind,
            cache_key=cache_key,
        )

    def inspect(
        self,
        *,
        task_id: str,
        producer_id: str,
        kind: str,
        cache_key: str,
    ) -> JsonArtifact:
        self._validate(kind, cache_key)
        path = (self._directory(task_id, producer_id) / f"{cache_key}.json").resolve(
            strict=True
        )
        path.relative_to(self.project_root)
        content = path.read_bytes()
        parsed = json.loads(content.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError("JSON artifact root must be an object")
        return JsonArtifact(
            producer_id=producer_id,
            kind=kind,
            cache_key=cache_key,
            relative_path=path.relative_to(self.project_root).as_posix(),
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    def load(self, artifact: JsonArtifact) -> dict[str, Any]:
        path = self.project_root.joinpath(*Path(artifact.relative_path).parts).resolve(
            strict=True
        )
        path.relative_to(self.project_root)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise RuntimeError("JSON artifact SHA-256 changed after registration")
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("JSON artifact root must be an object")
        return payload

    @staticmethod
    def register(
        session: Session,
        *,
        task_id: str,
        phase: str,
        artifact: JsonArtifact,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        row = session.scalar(
            select(Artifact).where(
                Artifact.task_id == task_id,
                Artifact.kind == artifact.kind,
                Artifact.cache_key == artifact.cache_key,
            )
        )
        values = {
            "producer_id": artifact.producer_id,
            **(metadata or {}),
        }
        if row is None:
            session.add(
                Artifact(
                    task_id=task_id,
                    sample_id=None,
                    kind=artifact.kind,
                    phase=phase,
                    cache_key=artifact.cache_key,
                    path=artifact.relative_path,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    state=ArtifactState.READY.value,
                    metadata_json=values,
                )
            )
            return
        row.path = artifact.relative_path
        row.sha256 = artifact.sha256
        row.size_bytes = artifact.size_bytes
        row.state = ArtifactState.READY.value
        row.metadata_json = values

    def registered(
        self,
        session: Session,
        *,
        task_id: str,
        producer_id: str,
        kind: str,
        cache_key: str,
    ) -> JsonArtifact | None:
        row = session.scalar(
            select(Artifact).where(
                Artifact.task_id == task_id,
                Artifact.kind == kind,
                Artifact.cache_key == cache_key,
                Artifact.state == ArtifactState.READY.value,
            )
        )
        if row is None:
            return None
        artifact = self.inspect(
            task_id=task_id,
            producer_id=producer_id,
            kind=kind,
            cache_key=cache_key,
        )
        if (
            row.path != artifact.relative_path
            or row.sha256 != artifact.sha256
            or row.size_bytes != artifact.size_bytes
            or row.metadata_json.get("producer_id") != producer_id
        ):
            raise RuntimeError("Registered JSON artifact changed on disk")
        return artifact

    def _directory(self, task_id: str, producer_id: str) -> Path:
        if not task_id or any(character not in "0123456789abcdef-" for character in task_id):
            raise ValueError("Task id is unsafe for an artifact path")
        if not _SAFE_ID.fullmatch(producer_id):
            raise ValueError(f"Unsafe artifact producer id: {producer_id}")
        path = (
            self.project_root
            / "data"
            / "tasks"
            / task_id
            / "artifacts"
            / producer_id.replace(".", "_")
        ).resolve(strict=False)
        path.relative_to(self.project_root)
        return path

    @staticmethod
    def _validate(kind: str, cache_key: str) -> None:
        if not _SAFE_KIND.fullmatch(kind):
            raise ValueError(f"Unsafe artifact kind: {kind}")
        if not _SHA256.fullmatch(cache_key):
            raise ValueError("JSON artifact cache key must be SHA-256")
