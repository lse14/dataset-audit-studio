from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.jobs.errors import InvalidTaskTransition, StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.scoring.assets import build_component_identities
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.repository import ScoringRepository
from dataset_audit_studio.scoring.types import (
    RuntimeAssets,
    SampleInput,
    SampleScore,
    ScoringRuntime,
)

RuntimeFactory = Callable[[ScoringConfig, RuntimeAssets], ScoringRuntime]


@dataclass(frozen=True)
class ScoringSummary:
    task_id: str
    eligible_samples: int
    processed_samples: int
    inferred_samples: int
    cached_samples: int
    resumed_from_index: int
    final_status: str


class ModelScorer:
    def __init__(
        self,
        tasks: TaskService,
        *,
        repository: ScoringRepository | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self.tasks = tasks
        self.repository = repository or ScoringRepository()
        self.runtime_factory = runtime_factory or self._default_runtime_factory

    def run(self, token: WorkerToken, assets: RuntimeAssets) -> ScoringSummary:
        control = self.tasks.honor_claimed_control_before_work(
            token,
            phase=TaskStatus.MODEL_SCORING,
        )
        if control is not None:
            return ScoringSummary(token.task_id, 0, 0, 0, 0, 0, control.status)
        task = self.tasks.get_task(token.task_id)
        config = ScoringConfig.from_task_config(task.config)
        identities = build_component_identities(config, assets)
        identity_digest = self._identity_digest(identities)
        checkpoints = [
            checkpoint
            for checkpoint in self.tasks.list_checkpoints(
                task.id, phase=TaskStatus.MODEL_SCORING.value
            )
            if checkpoint.config_hash == task.config_hash
        ]
        scoring_checkpoints = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.cursor.get("asset_wait") is not True
        ]
        last_scoring = scoring_checkpoints[-1] if scoring_checkpoints else None
        start_index = int(last_scoring.cursor.get("next_index", 0)) if last_scoring else 0
        if last_scoring is not None:
            previous_digest = str(last_scoring.cursor.get("identity_digest", ""))
            if previous_digest != identity_digest:
                raise ValueError("Scoring model identity changed since the last checkpoint")

        with self.tasks.database.read_session() as session:
            samples = self.repository.list_inputs(session, task)
        if not 0 <= start_index <= len(samples):
            raise ValueError("Scoring checkpoint index is outside the eligible sample list")

        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        first_config_batch = last_scoring is None
        processed = start_index
        inferred = int(last_scoring.cursor.get("inferred_samples", 0)) if last_scoring else 0
        cached_count = int(last_scoring.cursor.get("cached_samples", 0)) if last_scoring else 0
        runtime: ScoringRuntime | None = None
        resumed_from = start_index
        ranges = list(range(start_index, len(samples), config.batch_size))
        if not ranges and first_config_batch:
            ranges = [start_index]

        try:
            for batch_start in ranges:
                batch_end = min(batch_start + config.batch_size, len(samples))
                batch = samples[batch_start:batch_end]
                self._verify_sources(batch)
                with self.tasks.database.read_session() as session:
                    cached = self.repository.cached_results(session, batch, identities)
                fully_cached = all(
                    set(cached[item.sample_id]) == set(identities) for item in batch
                )
                if fully_cached:
                    scores = tuple(
                        SampleScore(sample_id=item.sample_id, results=cached[item.sample_id])
                        for item in batch
                    )
                    cached_count += len(batch)
                elif batch:
                    if runtime is None:
                        runtime = self.runtime_factory(config, assets)
                    scores = runtime.score_batch(batch)
                    self._validate_scores(batch, scores, identities)
                    inferred += len(batch)
                else:
                    scores = ()

                processed = batch_end
                cursor = {
                    "next_index": batch_end,
                    "identity_digest": identity_digest,
                    "enabled_components": list(config.enabled_components),
                    "inferred_samples": inferred,
                    "cached_samples": cached_count,
                }

                def write_batch(
                    session,
                    *,
                    batch_scores=scores,
                    prepare=first_config_batch,
                ) -> None:
                    self.repository.persist_batch(
                        session,
                        task_id=task.id,
                        scores=batch_scores,
                        identities=identities,
                        config=config,
                        prepare=prepare,
                    )

                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.MODEL_SCORING,
                    config_hash=task.config_hash,
                    batch_index=batch_index,
                    completed_items=batch_end,
                    progress_total=len(samples),
                    cursor=cursor,
                    lease_seconds=300,
                    batch_writer=write_batch,
                )
                first_config_batch = False
                batch_index += 1
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        samples,
                        processed,
                        inferred,
                        cached_count,
                        resumed_from,
                        result.task.status,
                    )

            final_status = self._complete_or_honor_control(
                token,
                task_config_hash=task.config_hash,
                batch_index=batch_index,
                completed_items=len(samples),
                cursor={
                    "next_index": len(samples),
                    "identity_digest": identity_digest,
                    "enabled_components": list(config.enabled_components),
                    "inferred_samples": inferred,
                    "cached_samples": cached_count,
                },
            )
            return self._summary(
                task.id,
                samples,
                len(samples),
                inferred,
                cached_count,
                resumed_from,
                final_status,
            )
        finally:
            if runtime is not None:
                runtime.close()

    @staticmethod
    def _default_runtime_factory(
        config: ScoringConfig, assets: RuntimeAssets
    ) -> ScoringRuntime:
        from dataset_audit_studio.scoring.torch_runtime import TorchScoringRuntime

        return TorchScoringRuntime(config, assets)

    @staticmethod
    def _verify_sources(samples: tuple[SampleInput, ...]) -> None:
        for sample in samples:
            stat = sample.source_path.stat()
            if stat.st_size != sample.source_size or stat.st_mtime_ns != sample.source_mtime_ns:
                raise RuntimeError(
                    f"Source changed after scanning: {sample.relative_path}; rescan before scoring"
                )
            if not sample.image_path.is_file():
                raise RuntimeError(f"Scoring image is missing: {sample.relative_path}")

    @staticmethod
    def _validate_scores(
        samples: tuple[SampleInput, ...],
        scores: tuple[SampleScore, ...],
        identities,
    ) -> None:
        expected_ids = [sample.sample_id for sample in samples]
        actual_ids = [score.sample_id for score in scores]
        if actual_ids != expected_ids:
            raise RuntimeError("Scoring runtime returned samples out of order")
        expected_components = set(identities)
        for score in scores:
            if set(score.results) != expected_components:
                raise RuntimeError(
                    f"Scoring runtime returned incomplete components for {score.sample_id}"
                )

    def _complete_or_honor_control(
        self,
        token: WorkerToken,
        *,
        task_config_hash: str,
        batch_index: int,
        completed_items: int,
        cursor: dict,
    ) -> str:
        current = self.tasks.get_task(token.task_id)
        if current.status in {
            TaskStatus.PAUSING.value,
            TaskStatus.TERMINATING.value,
        }:
            return self.tasks.commit_batch(
                token,
                phase=TaskStatus.MODEL_SCORING,
                config_hash=task_config_hash,
                batch_index=batch_index,
                completed_items=completed_items,
                progress_total=completed_items,
                cursor={**cursor, "control_only": True},
                lease_seconds=300,
            ).task.status
        try:
            return self.tasks.complete_phase(
                token, phase=TaskStatus.MODEL_SCORING
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
            return self.tasks.commit_batch(
                token,
                phase=TaskStatus.MODEL_SCORING,
                config_hash=task_config_hash,
                batch_index=batch_index,
                completed_items=completed_items,
                progress_total=completed_items,
                cursor={**cursor, "control_only": True},
                lease_seconds=300,
            ).task.status

    @staticmethod
    def _identity_digest(identities: dict) -> str:
        payload = {
            component: {
                "model_id": identity.model_id,
                "model_sha256": identity.model_sha256,
                "preprocessing_version": identity.preprocessing_version,
                "config_hash": identity.config_hash,
            }
            for component, identity in sorted(identities.items())
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _summary(
        task_id: str,
        samples: tuple[SampleInput, ...],
        processed: int,
        inferred: int,
        cached_count: int,
        resumed_from: int,
        status: str,
    ) -> ScoringSummary:
        return ScoringSummary(
            task_id=task_id,
            eligible_samples=len(samples),
            processed_samples=processed,
            inferred_samples=inferred,
            cached_samples=cached_count,
            resumed_from_index=resumed_from,
            final_status=status,
        )
