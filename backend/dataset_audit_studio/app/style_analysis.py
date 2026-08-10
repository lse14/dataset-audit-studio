from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dataset_audit_studio.adapters.style_repository import StyleRepository
from dataset_audit_studio.components.artist_style.algorithm import analyze_artist_scope
from dataset_audit_studio.components.artist_style.assets import style_identity
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.components.artist_style.contracts import (
    StyleFeatureBatch,
    StyleFeatureRuntime,
    StyleScope,
)
from dataset_audit_studio.components.artist_style.runtime import TorchStyleRuntime
from dataset_audit_studio.core.model_assets import RuntimeAssets
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.jobs.errors import InvalidTaskTransition, StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT

RuntimeFactory = Callable[[StyleConfig, RuntimeAssets], StyleFeatureRuntime]


@dataclass(frozen=True)
class StyleSummary:
    task_id: str
    scopes: int
    eligible_samples: int
    processed_samples: int
    inferred_samples: int
    cached_samples: int
    resumed_from_scope: int
    final_status: str


class _ScopeBuffer:
    def __init__(self, root: Path, rows: int) -> None:
        self.root = root
        self.rows = rows
        self.paths: list[Path] = []
        self.lsnet: np.memmap | None = None
        self.gram: np.memmap | None = None
        self.dino: np.memmap | None = None
        self.colors: np.memmap | None = None

    def write(self, start: int, batch: StyleFeatureBatch) -> None:
        rows = len(batch.sample_ids)
        if start < 0 or start + rows > self.rows:
            raise ValueError("Style feature batch exceeds its scope buffer")
        if self.gram is None:
            self.lsnet = self._map("lsnet", batch.lsnet.shape[1])
            self.gram = self._map("gram", batch.gram.shape[1])
            self.dino = self._map("dino", batch.dino.shape[1])
            self.colors = self._map("color", batch.color_histogram.shape[1])
        assert self.lsnet is not None and self.dino is not None and self.colors is not None
        if (
            batch.lsnet.shape != (rows, self.lsnet.shape[1])
            or batch.gram.shape != (rows, self.gram.shape[1])
            or batch.dino.shape != (rows, self.dino.shape[1])
            or batch.color_histogram.shape != (rows, self.colors.shape[1])
        ):
            raise ValueError("Style feature dimensions changed within a scope")
        self.lsnet[start : start + rows] = batch.lsnet
        self.gram[start : start + rows] = batch.gram
        self.dino[start : start + rows] = batch.dino
        self.colors[start : start + rows] = batch.color_histogram

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if self.lsnet is None or self.gram is None or self.dino is None or self.colors is None:
            raise RuntimeError("Style scope buffer is empty")
        self.lsnet.flush()
        self.gram.flush()
        self.dino.flush()
        self.colors.flush()
        return self.lsnet, self.gram, self.dino, self.colors

    def close(self) -> None:
        for name in ("lsnet", "gram", "dino", "colors"):
            value = getattr(self, name)
            if value is not None:
                value.flush()
                value._mmap.close()  # noqa: SLF001 - NumPy exposes no public memmap close
                setattr(self, name, None)
        for path in self.paths:
            path.unlink(missing_ok=True)

    def _map(self, label: str, dimensions: int) -> np.memmap:
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f"{label}-",
            suffix=".part",
            dir=self.root,
        )
        os.close(descriptor)
        path = Path(raw_path).resolve(strict=True)
        path.relative_to(self.root.resolve(strict=True))
        self.paths.append(path)
        return np.memmap(
            path,
            mode="w+",
            dtype=np.float32,
            shape=(self.rows, dimensions),
        )


class StyleAnalyzer:
    def __init__(
        self,
        tasks: TaskService,
        *,
        repository: StyleRepository | None = None,
        runtime_factory: RuntimeFactory | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.tasks = tasks
        self.repository = repository or StyleRepository(project_root=project_root)
        self.runtime_factory = runtime_factory or self._default_runtime_factory
        self.project_root = project_root.resolve(strict=False)

    def run(self, token: WorkerToken, assets: RuntimeAssets) -> StyleSummary:
        control = self.tasks.honor_claimed_control_before_work(
            token,
            phase=TaskStatus.STYLE_ANALYSIS,
        )
        if control is not None:
            return StyleSummary(token.task_id, 0, 0, 0, 0, 0, 0, control.status)
        task = self.tasks.get_task(token.task_id)
        config = StyleConfig.from_task_config(task.config)
        model_sha256, analysis_hash = style_identity(config, assets) if config.enabled else (
            hashlib.sha256(b"style-disabled").hexdigest(),
            hashlib.sha256(b"style-disabled").hexdigest(),
        )
        identity_digest = hashlib.sha256(
            f"{model_sha256}\0{analysis_hash}".encode()
        ).hexdigest()
        checkpoints = [
            checkpoint
            for checkpoint in self.tasks.list_checkpoints(
                task.id, phase=TaskStatus.STYLE_ANALYSIS.value
            )
            if checkpoint.config_hash == task.config_hash
        ]
        work_checkpoints = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.cursor.get("asset_wait") is not True
        ]
        last_work = work_checkpoints[-1] if work_checkpoints else None
        if last_work is not None and last_work.cursor.get("identity_digest") != identity_digest:
            raise ValueError("Style model identity changed since the last checkpoint")
        with self.tasks.database.read_session() as session:
            scopes = self.repository.list_scopes(session, task) if config.enabled else ()
        scope_digest = self._scope_digest(scopes)
        if last_work is not None and last_work.cursor.get("scope_digest") != scope_digest:
            raise ValueError("Style scope membership changed; rescan before resuming")
        start_scope = int(last_work.cursor.get("next_scope", 0)) if last_work else 0
        if not 0 <= start_scope <= len(scopes):
            raise ValueError("Style checkpoint scope is outside the current scope list")
        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        first_config_batch = not (
            last_work is not None and last_work.cursor.get("prepared") is True
        )
        completed = sum(len(scope.samples) for scope in scopes[:start_scope])
        inferred = int(last_work.cursor.get("inferred_samples", 0)) if last_work else 0
        cached = int(last_work.cursor.get("cached_samples", 0)) if last_work else 0
        total = sum(len(scope.samples) for scope in scopes)
        runtime: StyleFeatureRuntime | None = None
        temp_root = self._temp_root(task.id)

        try:
            if not scopes and first_config_batch:
                cursor = self._cursor(
                    next_scope=0,
                    identity_digest=identity_digest,
                    scope_digest=scope_digest,
                    inferred=inferred,
                    cached=cached,
                    prepared=True,
                )

                def prepare_empty(session) -> None:
                    self.repository.prepare_empty(session, task.id)

                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.STYLE_ANALYSIS,
                    config_hash=task.config_hash,
                    batch_index=batch_index,
                    completed_items=0,
                    progress_total=0,
                    cursor=cursor,
                    lease_seconds=300,
                    batch_writer=prepare_empty,
                )
                batch_index += 1
                first_config_batch = False
                if result.control_state != "continue":
                    return self._summary(
                        task.id, scopes, 0, inferred, cached, start_scope, result.task.status
                    )

            for scope_index in range(start_scope, len(scopes)):
                scope = scopes[scope_index]
                control = self._current_control(task.id)
                if control is not None:
                    status = self._commit_control(
                        token,
                        task.config_hash,
                        batch_index,
                        completed,
                        total,
                        self._cursor(
                            next_scope=scope_index,
                            identity_digest=identity_digest,
                            scope_digest=scope_digest,
                            inferred=inferred,
                            cached=cached,
                            prepared=not first_config_batch,
                        ),
                    )
                    return self._summary(
                        task.id,
                        scopes,
                        completed,
                        inferred,
                        cached,
                        start_scope,
                        status,
                    )
                scope_hash = self.repository.scope_config_hash(scope, config)
                with self.tasks.database.read_session() as session:
                    assessments = self.repository.cached_assessments(
                        session,
                        scope,
                        model_sha256=model_sha256,
                        config_hash=scope_hash,
                    )
                if assessments is not None:
                    cached += len(scope.samples)
                else:
                    if runtime is None:
                        runtime = self.runtime_factory(config, assets)
                    buffer = _ScopeBuffer(temp_root, len(scope.samples))
                    try:
                        for offset in range(0, len(scope.samples), config.batch_size):
                            batch_samples = scope.samples[offset : offset + config.batch_size]
                            self._verify_sources(batch_samples)
                            features = runtime.extract(batch_samples)
                            if features.sample_ids != tuple(
                                sample.sample_id for sample in batch_samples
                            ):
                                raise RuntimeError(
                                    "Style runtime returned samples out of order"
                                )
                            buffer.write(offset, features)
                            if self._current_control(task.id) is not None:
                                status = self._commit_control(
                                    token,
                                    task.config_hash,
                                    batch_index,
                                    completed,
                                    total,
                                    self._cursor(
                                        next_scope=scope_index,
                                        identity_digest=identity_digest,
                                        scope_digest=scope_digest,
                                        inferred=inferred,
                                        cached=cached,
                                        prepared=not first_config_batch,
                                    ),
                                )
                                return self._summary(
                                    task.id,
                                    scopes,
                                    completed,
                                    inferred,
                                    cached,
                                    start_scope,
                                    status,
                                )
                        lsnet, gram, dino, colors = buffer.arrays()
                        assessments = analyze_artist_scope(
                            tuple(sample.sample_id for sample in scope.samples),
                            lsnet,
                            gram,
                            dino,
                            colors,
                            config,
                        )
                        inferred += len(scope.samples)
                    finally:
                        buffer.close()
                completed += len(scope.samples)
                cursor = self._cursor(
                    next_scope=scope_index + 1,
                    identity_digest=identity_digest,
                    scope_digest=scope_digest,
                    inferred=inferred,
                    cached=cached,
                    prepared=True,
                )

                def write_scope(
                    session,
                    *,
                    current_scope=scope,
                    current_assessments=assessments,
                    current_hash=scope_hash,
                    prepare=first_config_batch,
                ) -> None:
                    assert current_assessments is not None
                    self.repository.persist_scope(
                        session,
                        task_id=task.id,
                        scope=current_scope,
                        assessments=current_assessments,
                        model_sha256=model_sha256,
                        config_hash=current_hash,
                        config=config,
                        prepare=prepare,
                    )

                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.STYLE_ANALYSIS,
                    config_hash=task.config_hash,
                    batch_index=batch_index,
                    completed_items=completed,
                    progress_total=total,
                    cursor=cursor,
                    lease_seconds=300,
                    batch_writer=write_scope,
                )
                batch_index += 1
                first_config_batch = False
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        scopes,
                        completed,
                        inferred,
                        cached,
                        start_scope,
                        result.task.status,
                    )
            status = self._complete_or_control(
                token,
                task.config_hash,
                batch_index,
                total,
                self._cursor(
                    next_scope=len(scopes),
                    identity_digest=identity_digest,
                    scope_digest=scope_digest,
                    inferred=inferred,
                    cached=cached,
                    prepared=True,
                ),
            )
            return self._summary(
                task.id, scopes, total, inferred, cached, start_scope, status
            )
        finally:
            if runtime is not None:
                runtime.close()
            if temp_root.is_dir() and not any(temp_root.iterdir()):
                temp_root.rmdir()

    @staticmethod
    def _default_runtime_factory(
        config: StyleConfig, assets: RuntimeAssets
    ) -> StyleFeatureRuntime:
        return TorchStyleRuntime(config, assets)

    @staticmethod
    def _scope_digest(scopes: tuple[StyleScope, ...]) -> str:
        payload = [
            [
                scope.scope_id,
                [[sample.sample_id, sample.pixel_sha256] for sample in scope.samples],
            ]
            for scope in scopes
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _temp_root(self, task_id: str) -> Path:
        path = (self.project_root / "data" / "tasks" / task_id / "style" / "tmp").resolve(
            strict=False
        )
        path.relative_to(self.project_root)
        return path

    @staticmethod
    def _verify_sources(samples) -> None:
        for sample in samples:
            stat = sample.source_path.stat()
            if stat.st_size != sample.source_size or stat.st_mtime_ns != sample.source_mtime_ns:
                raise RuntimeError(
                    f"Source changed after scanning: {sample.relative_path}; rescan required"
                )
            if not sample.image_path.is_file():
                raise RuntimeError(f"Style image is missing: {sample.relative_path}")

    def _current_control(self, task_id: str) -> str | None:
        status = self.tasks.get_task(task_id).status
        return (
            status
            if status in {TaskStatus.PAUSING.value, TaskStatus.TERMINATING.value}
            else None
        )

    def _commit_control(
        self,
        token: WorkerToken,
        config_hash: str,
        batch_index: int,
        completed: int,
        total: int,
        cursor: dict,
    ) -> str:
        return self.tasks.commit_batch(
            token,
            phase=TaskStatus.STYLE_ANALYSIS,
            config_hash=config_hash,
            batch_index=batch_index,
            completed_items=completed,
            progress_total=total,
            cursor={**cursor, "control_only": True},
            lease_seconds=300,
        ).task.status

    def _complete_or_control(
        self,
        token: WorkerToken,
        config_hash: str,
        batch_index: int,
        completed: int,
        cursor: dict,
    ) -> str:
        current = self.tasks.get_task(token.task_id)
        if current.status in {
            TaskStatus.PAUSING.value,
            TaskStatus.TERMINATING.value,
        }:
            return self._commit_control(
                token, config_hash, batch_index, completed, completed, cursor
            )
        try:
            return self.tasks.complete_phase(
                token, phase=TaskStatus.STYLE_ANALYSIS
            ).status
        except StaleWorkerToken:
            return self.tasks.get_task(token.task_id).status
        except InvalidTaskTransition:
            current = self.tasks.get_task(token.task_id)
            if current.status not in {
                TaskStatus.PAUSING.value,
                TaskStatus.TERMINATING.value,
            }:
                raise
            return self._commit_control(
                token, config_hash, batch_index, completed, completed, cursor
            )

    @staticmethod
    def _cursor(
        *,
        next_scope: int,
        identity_digest: str,
        scope_digest: str,
        inferred: int,
        cached: int,
        prepared: bool,
    ) -> dict:
        return {
            "next_scope": next_scope,
            "identity_digest": identity_digest,
            "scope_digest": scope_digest,
            "inferred_samples": inferred,
            "cached_samples": cached,
            "prepared": prepared,
        }

    @staticmethod
    def _summary(
        task_id: str,
        scopes: tuple[StyleScope, ...],
        processed: int,
        inferred: int,
        cached: int,
        resumed_from: int,
        status: str,
    ) -> StyleSummary:
        return StyleSummary(
            task_id=task_id,
            scopes=len(scopes),
            eligible_samples=sum(len(scope.samples) for scope in scopes),
            processed_samples=processed,
            inferred_samples=inferred,
            cached_samples=cached,
            resumed_from_scope=resumed_from,
            final_status=status,
        )
