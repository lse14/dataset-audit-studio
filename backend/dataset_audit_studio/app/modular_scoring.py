from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from dataset_audit_studio.adapters.safetensor_features import SafetensorFeatureStore
from dataset_audit_studio.components.aesthetic_domain.config import AestheticDomainConfig
from dataset_audit_studio.components.aesthetic_domain.runtime import AestheticDomainRuntime
from dataset_audit_studio.components.ai_detection.config import (
    UFD_MODEL_ID,
    AIDetectionConfig,
)
from dataset_audit_studio.components.ai_detection.runtime import AIDetectionRuntime
from dataset_audit_studio.components.clip_features.config import ClipFeatureConfig
from dataset_audit_studio.components.clip_features.runtime import (
    AESTHETIC_FEATURE_CAPABILITY,
    CLIP_MODEL_ID,
    UFD_FEATURE_CAPABILITY,
    ClipFeatureRuntime,
)
from dataset_audit_studio.components.ocr_evidence.config import OCREvidenceConfig
from dataset_audit_studio.components.ocr_evidence.runtime import OCREvidenceRuntime
from dataset_audit_studio.components.watermark_evidence.config import WatermarkEvidenceConfig
from dataset_audit_studio.components.watermark_evidence.runtime import (
    WatermarkEvidenceRuntime,
)
from dataset_audit_studio.core.feature_store import FeatureShard, FeatureStore
from dataset_audit_studio.core.model_assets import RuntimeAssets, runtime_model_digest
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.jobs.errors import InvalidTaskTransition, StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scoring.assets import (
    EVIDENCE_SOURCES,
    JTP3_MODEL_ID,
    OCR_DET_MODEL_ID,
    OCR_REC_MODEL_ID,
    PREPROCESSING_VERSIONS,
    WAIFU_MODEL_ID,
    WATERMARK_MODEL_ID,
    ai_evidence_source,
    ai_preprocessing_version,
)
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.repository import ScoringRepository
from dataset_audit_studio.scoring.types import ComponentIdentity, SampleInput, SampleScore

CLIP_COMPONENT_ID = "feature.clip_l14"
SCORING_COMPONENT_KEYS = {
    "score.aesthetic_domain": "aesthetic",
    "detect.ai": "ai",
    "evidence.ocr": "ocr",
    "evidence.watermark": "watermark",
}
MODULAR_SCORING_COMPONENT_IDS = frozenset((CLIP_COMPONENT_ID, *SCORING_COMPONENT_KEYS))
CLIP_PREPROCESSING_VERSION = "openai-clip-l14-dual-preprocess-v1"

RuntimeFactory = Callable[[ScoringConfig, RuntimeAssets], Any]


@dataclass(frozen=True)
class ModularScoringSummary:
    task_id: str
    component_id: str
    eligible_samples: int
    processed_samples: int
    inferred_samples: int
    cached_samples: int
    resumed_from_index: int
    component_complete: bool
    final_status: str


class ModularScoringComponentService:
    def __init__(
        self,
        tasks: TaskService,
        *,
        repository: ScoringRepository | None = None,
        feature_store: FeatureStore | None = None,
        runtime_factories: dict[str, RuntimeFactory] | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.tasks = tasks
        self.repository = repository or ScoringRepository(project_root=project_root)
        self.feature_store = feature_store or SafetensorFeatureStore(project_root=project_root)
        self.runtime_factories = runtime_factories or {}

    def run(
        self,
        token: WorkerToken,
        assets: RuntimeAssets,
        *,
        component_id: str,
        component_order: tuple[str, ...],
    ) -> ModularScoringSummary:
        if component_id not in MODULAR_SCORING_COMPONENT_IDS:
            raise ValueError(f"Unsupported modular scoring component: {component_id}")
        if component_id not in component_order or len(component_order) != len(set(component_order)):
            raise ValueError("Scoring component order is invalid")
        control = self.tasks.honor_claimed_control_before_work(
            token,
            phase=TaskStatus.MODEL_SCORING,
        )
        if control is not None:
            return ModularScoringSummary(
                token.task_id,
                component_id,
                0,
                0,
                0,
                0,
                0,
                False,
                control.status,
            )

        task = self.tasks.get_task(token.task_id)
        config = ScoringConfig.from_task_config(task.config)
        legacy_key = SCORING_COMPONENT_KEYS.get(component_id)
        if legacy_key is not None and legacy_key not in config.enabled_components:
            raise ValueError(f"Disabled scoring component was scheduled: {component_id}")
        capabilities = self._clip_capabilities(config)
        if component_id == CLIP_COMPONENT_ID and not capabilities:
            raise ValueError("CLIP feature provider has no enabled consumers")
        checkpoints = [
            checkpoint
            for checkpoint in self.tasks.list_checkpoints(
                task.id,
                phase=TaskStatus.MODEL_SCORING.value,
            )
            if checkpoint.config_hash == task.config_hash
        ]
        if component_id == CLIP_COMPONENT_ID:
            clip_model_digest = runtime_model_digest(assets, (CLIP_MODEL_ID,))
        elif component_id == "score.aesthetic_domain" or (
            component_id == "detect.ai" and config.ai.model_id == UFD_MODEL_ID
        ):
            clip_model_digest = self._registered_clip_model_digest(checkpoints)
        else:
            clip_model_digest = None
        component_config, identity = self._component_identity(
            component_id,
            config,
            assets,
            clip_model_digest,
        )
        identity_digest = self._identity_digest(
            component_id,
            identity,
            config,
            clip_model_digest,
        )
        component_checkpoints = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.cursor.get("modular_scoring") is True
            and checkpoint.cursor.get("component_id") == component_id
        ]
        last = component_checkpoints[-1] if component_checkpoints else None
        if last is not None and last.cursor.get("identity_digest") != identity_digest:
            raise ValueError(f"Scoring identity changed while resuming {component_id}")

        with self.tasks.database.read_session() as session:
            samples = self.repository.list_inputs(session, task)
        start_index = int(last.cursor.get("next_index", 0)) if last else 0
        if not 0 <= start_index <= len(samples):
            raise ValueError("Component checkpoint index is outside the eligible sample list")
        if last is not None and last.cursor.get("component_complete") is True:
            return self._summary(
                task.id,
                component_id,
                samples,
                len(samples),
                int(last.cursor.get("inferred_samples", 0)),
                int(last.cursor.get("cached_samples", 0)),
                start_index,
                True,
                task.status,
            )

        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        inferred = int(last.cursor.get("inferred_samples", 0)) if last else 0
        cached_count = int(last.cursor.get("cached_samples", 0)) if last else 0
        processed = start_index
        resumed_from = start_index
        results_prepared = any(
            checkpoint.cursor.get("results_prepared") is True for checkpoint in checkpoints
        )
        runtime = None
        ranges = list(range(start_index, len(samples), config.batch_size))
        if not ranges:
            ranges = [start_index]
        component_index = component_order.index(component_id)
        total_progress = len(samples) * len(component_order)
        progress_floor = task.progress_current

        try:
            for batch_start in ranges:
                batch_end = min(batch_start + config.batch_size, len(samples))
                batch = samples[batch_start:batch_end]
                self._verify_sources(batch)
                scores: tuple[SampleScore, ...] = ()
                clip_shard: FeatureShard | None = None
                if component_id == CLIP_COMPONENT_ID:
                    clip_shard = self._expected_clip_shard(
                        task.id,
                        batch,
                        capabilities,
                        clip_model_digest,
                        checkpoints=checkpoints,
                        require_registered=False,
                    )
                    if clip_shard is not None:
                        cached_count += len(batch)
                    elif batch:
                        if runtime is None:
                            runtime = self._runtime(component_id, component_config, assets)
                        images = self._load_images(batch)
                        try:
                            features = runtime.extract(
                                images,
                                tuple(sample.sample_id for sample in batch),
                                capabilities,
                            )
                        finally:
                            self._close_images(images)
                        clip_shard = self.feature_store.write(
                            task_id=task.id,
                            producer_id=CLIP_COMPONENT_ID,
                            sample_ids=tuple(sample.sample_id for sample in batch),
                            pixel_hashes=tuple(sample.pixel_sha256 for sample in batch),
                            features=dict(features.features),
                            model_digest=clip_model_digest,
                            preprocessing_version=CLIP_PREPROCESSING_VERSION,
                        )
                        inferred += len(batch)
                else:
                    assert legacy_key is not None and identity is not None
                    identities = {legacy_key: identity}
                    with self.tasks.database.read_session() as session:
                        cached = self.repository.cached_results(session, batch, identities)
                    fully_cached = all(
                        set(cached[sample.sample_id]) == {legacy_key} for sample in batch
                    )
                    if fully_cached:
                        scores = tuple(
                            SampleScore(
                                sample_id=sample.sample_id,
                                results={legacy_key: cached[sample.sample_id][legacy_key]},
                            )
                            for sample in batch
                        )
                        cached_count += len(batch)
                    elif batch:
                        if runtime is None:
                            runtime = self._runtime(component_id, component_config, assets)
                        values = self._infer(
                            component_id,
                            runtime,
                            component_config,
                            task.id,
                            batch,
                            capabilities,
                            clip_model_digest,
                            checkpoints,
                        )
                        if len(values) != len(batch):
                            raise RuntimeError(
                                f"Component {component_id} returned an incomplete batch"
                            )
                        scores = tuple(
                            SampleScore(sample.sample_id, {legacy_key: value})
                            for sample, value in zip(batch, values, strict=True)
                        )
                        self._require_finite([score.results for score in scores])
                        inferred += len(batch)

                processed = batch_end
                complete = batch_end == len(samples)
                next_results_prepared = results_prepared or component_id != CLIP_COMPONENT_ID
                cursor = {
                    "modular_scoring": True,
                    "component_id": component_id,
                    "component_order": list(component_order),
                    "next_index": batch_end,
                    "component_complete": complete,
                    "identity_digest": identity_digest,
                    "inferred_samples": inferred,
                    "cached_samples": cached_count,
                    "results_prepared": next_results_prepared,
                }
                if component_id == CLIP_COMPONENT_ID:
                    cursor.update(
                        {
                            "feature_model_digest": clip_model_digest,
                            "feature_preprocessing_version": CLIP_PREPROCESSING_VERSION,
                            "feature_capabilities": list(capabilities),
                        }
                    )
                if clip_shard is not None:
                    cursor["feature_shard"] = {
                        "cache_key": clip_shard.cache_key,
                        "relative_path": clip_shard.relative_path,
                        "sha256": clip_shard.sha256,
                    }

                def write_batch(
                    session,
                    *,
                    current_scores=scores,
                    prepare=not results_prepared and component_id != CLIP_COMPONENT_ID,
                ) -> None:
                    if component_id == CLIP_COMPONENT_ID:
                        return
                    assert legacy_key is not None and identity is not None
                    self.repository.persist_batch(
                        session,
                        task_id=task.id,
                        scores=current_scores,
                        identities={legacy_key: identity},
                        config=component_config,
                        prepare=prepare,
                    )

                logical_progress = component_index * len(samples) + batch_end
                completed_items = max(progress_floor, logical_progress)
                progress_total = max(progress_floor, total_progress)
                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.MODEL_SCORING,
                    config_hash=task.config_hash,
                    batch_index=batch_index,
                    completed_items=completed_items,
                    progress_total=progress_total,
                    cursor=cursor,
                    lease_seconds=300,
                    batch_writer=write_batch,
                )
                batch_index += 1
                results_prepared = next_results_prepared
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        component_id,
                        samples,
                        processed,
                        inferred,
                        cached_count,
                        resumed_from,
                        complete,
                        result.task.status,
                    )
            return self._summary(
                task.id,
                component_id,
                samples,
                processed,
                inferred,
                cached_count,
                resumed_from,
                True,
                self.tasks.get_task(task.id).status,
            )
        finally:
            if runtime is not None:
                runtime.close()

    def _component_identity(
        self,
        component_id: str,
        config: ScoringConfig,
        assets: RuntimeAssets,
        clip_model_digest: str | None,
    ) -> tuple[ScoringConfig, ComponentIdentity | None]:
        if component_id == CLIP_COMPONENT_ID:
            return config, None
        legacy_key = SCORING_COMPONENT_KEYS[component_id]
        payload = config.model_dump(mode="python")
        for key in ("aesthetic", "ai", "ocr", "watermark"):
            payload[key]["enabled"] = key == legacy_key
        component_config = ScoringConfig.model_validate(payload)
        model_ids = self._component_model_ids(component_id, component_config)
        component_model_digest = runtime_model_digest(assets, model_ids)
        if component_id == "score.aesthetic_domain" or (
            component_id == "detect.ai" and component_config.ai.model_id == UFD_MODEL_ID
        ):
            if clip_model_digest is None:
                raise RuntimeError(f"{component_id} requires a committed CLIP model identity")
            digest = hashlib.sha256(
                f"{component_id}\0{component_model_digest}\0{clip_model_digest}".encode()
            ).hexdigest()
        else:
            digest = component_model_digest
        result_model_ids = {
            "aesthetic": component_config.aesthetic.model_id,
            "ai": component_config.ai.model_id,
            "ocr": "ppocrv5_server_ocr",
            "watermark": WATERMARK_MODEL_ID,
        }
        return component_config, ComponentIdentity(
            component=legacy_key,
            model_id=result_model_ids[legacy_key],
            model_sha256=digest,
            preprocessing_version=(
                ai_preprocessing_version(component_config.ai.model_id)
                if legacy_key == "ai"
                else PREPROCESSING_VERSIONS[legacy_key]
            ),
            config_hash=component_config.inference_config_hash(legacy_key),
            evidence_source=(
                ai_evidence_source(component_config.ai.model_id)
                if legacy_key == "ai"
                else EVIDENCE_SOURCES[legacy_key]
            ),
        )

    @staticmethod
    def _component_model_ids(
        component_id: str,
        config: ScoringConfig,
    ) -> tuple[str, ...]:
        if component_id == "score.aesthetic_domain":
            return (config.aesthetic.model_id, JTP3_MODEL_ID, WAIFU_MODEL_ID)
        if component_id == "detect.ai":
            return (config.ai.model_id,)
        if component_id == "evidence.ocr":
            return (OCR_DET_MODEL_ID, OCR_REC_MODEL_ID)
        if component_id == "evidence.watermark":
            return (WATERMARK_MODEL_ID,)
        raise ValueError(f"No component model identity mapping for {component_id}")

    def _identity_digest(
        self,
        component_id: str,
        identity: ComponentIdentity | None,
        config: ScoringConfig,
        clip_model_digest: str | None,
    ) -> str:
        if component_id == CLIP_COMPONENT_ID:
            payload = {
                "component": component_id,
                "model_digest": clip_model_digest,
                "preprocessing": CLIP_PREPROCESSING_VERSION,
                "capabilities": self._clip_capabilities(config),
                "precision": config.precision,
            }
        else:
            assert identity is not None
            payload = {
                "component": component_id,
                "model_id": identity.model_id,
                "model_sha256": identity.model_sha256,
                "preprocessing": identity.preprocessing_version,
                "config_hash": identity.config_hash,
            }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _expected_clip_shard(
        self,
        task_id: str,
        samples: tuple[SampleInput, ...],
        capabilities: tuple[str, ...],
        clip_model_digest: str | None,
        *,
        checkpoints: list[Any],
        require_registered: bool,
    ) -> FeatureShard | None:
        sample_ids = tuple(sample.sample_id for sample in samples)
        pixel_hashes = tuple(sample.pixel_sha256 for sample in samples)
        if clip_model_digest is None:
            raise RuntimeError("CLIP feature artifact has no producer model identity")
        model_digest = clip_model_digest
        cache_key = self.feature_store.cache_key(
            sample_ids=sample_ids,
            pixel_hashes=pixel_hashes,
            capabilities=capabilities,
            model_digest=model_digest,
            preprocessing_version=CLIP_PREPROCESSING_VERSION,
        )
        shard = self.feature_store.try_inspect(
            task_id=task_id,
            producer_id=CLIP_COMPONENT_ID,
            cache_key=cache_key,
        )
        if shard is None:
            return None
        registered = self._registered_clip_shard(checkpoints, cache_key)
        if require_registered and registered is None:
            raise RuntimeError("Required CLIP feature shard has no committed checkpoint")
        if registered is not None:
            registered_sha, registered_path = registered
            if shard.sha256 != registered_sha or shard.relative_path != registered_path:
                raise RuntimeError("CLIP feature shard changed after its checkpoint")
        if (
            shard.sample_ids != sample_ids
            or shard.pixel_hashes != pixel_hashes
            or shard.capabilities != tuple(sorted(capabilities))
            or shard.model_digest != model_digest
            or shard.preprocessing_version != CLIP_PREPROCESSING_VERSION
        ):
            raise RuntimeError("CLIP feature shard identity does not match its request")
        return shard

    @staticmethod
    def _registered_clip_model_digest(checkpoints: list[Any]) -> str:
        digests = {
            checkpoint.cursor.get("feature_model_digest")
            for checkpoint in checkpoints
            if checkpoint.cursor.get("modular_scoring") is True
            and checkpoint.cursor.get("component_id") == CLIP_COMPONENT_ID
        }
        if not digests:
            raise RuntimeError("Required CLIP producer checkpoint is missing")
        if len(digests) != 1 or not all(
            isinstance(digest, str) and len(digest) == 64 for digest in digests
        ):
            raise RuntimeError("CLIP producer model checkpoint identity is invalid")
        return str(digests.pop())

    @staticmethod
    def _registered_clip_shard(
        checkpoints: list[Any],
        cache_key: str,
    ) -> tuple[str, str] | None:
        identities: set[tuple[str, str]] = set()
        for checkpoint in checkpoints:
            cursor = checkpoint.cursor
            descriptor = cursor.get("feature_shard")
            if (
                cursor.get("modular_scoring") is True
                and cursor.get("component_id") == CLIP_COMPONENT_ID
                and isinstance(descriptor, dict)
                and descriptor.get("cache_key") == cache_key
            ):
                sha256 = descriptor.get("sha256")
                relative_path = descriptor.get("relative_path")
                if not isinstance(sha256, str) or not isinstance(relative_path, str):
                    raise RuntimeError("CLIP feature checkpoint identity is invalid")
                identities.add((sha256, relative_path))
        if not identities:
            return None
        if len(identities) != 1:
            raise RuntimeError("CLIP feature checkpoint identities conflict")
        return identities.pop()

    def _infer(
        self,
        component_id: str,
        runtime,
        config: ScoringConfig,
        task_id: str,
        samples: tuple[SampleInput, ...],
        capabilities: tuple[str, ...],
        clip_model_digest: str | None,
        checkpoints: list[Any],
    ) -> list[dict[str, Any]]:
        if component_id == "score.aesthetic_domain" or (
            component_id == "detect.ai" and config.ai.model_id == UFD_MODEL_ID
        ):
            shard = self._expected_clip_shard(
                task_id,
                samples,
                capabilities,
                clip_model_digest,
                checkpoints=checkpoints,
                require_registered=True,
            )
            if shard is None:
                raise RuntimeError("Required CLIP feature shard is missing")
            feature_batch = self.feature_store.load(shard)
            if feature_batch.sample_ids != tuple(sample.sample_id for sample in samples):
                raise RuntimeError("CLIP feature shard samples are out of order")
            if component_id == "detect.ai":
                return runtime.score(feature_batch.get(UFD_FEATURE_CAPABILITY))
            images = self._load_images(samples)
            try:
                return runtime.score(
                    images,
                    feature_batch.get(AESTHETIC_FEATURE_CAPABILITY),
                )
            finally:
                self._close_images(images)
        images = self._load_images(samples)
        try:
            return runtime.score(images)
        finally:
            self._close_images(images)

    def _runtime(
        self,
        component_id: str,
        config: ScoringConfig,
        assets: RuntimeAssets,
    ):
        custom = self.runtime_factories.get(component_id)
        if custom is not None:
            return custom(config, assets)
        if component_id == CLIP_COMPONENT_ID:
            return ClipFeatureRuntime(
                ClipFeatureConfig(device=config.device, precision=config.precision),
                assets,
            )
        if component_id == "score.aesthetic_domain":
            return AestheticDomainRuntime(
                AestheticDomainConfig(
                    device=config.device,
                    precision=config.precision,
                    model_id=config.aesthetic.model_id,
                    jtp_max_sequence=config.aesthetic.jtp_max_sequence,
                ),
                assets,
            )
        if component_id == "detect.ai":
            return AIDetectionRuntime(
                AIDetectionConfig(
                    device=config.device,
                    precision=config.precision,
                    model_id=config.ai.model_id,
                ),
                assets,
            )
        if component_id == "evidence.ocr":
            return OCREvidenceRuntime(
                OCREvidenceConfig(
                    device=config.device,
                    precision=config.precision,
                    bitmap_threshold=config.ocr.bitmap_threshold,
                    box_threshold=config.ocr.box_threshold,
                    unclip_ratio=config.ocr.unclip_ratio,
                    min_size=config.ocr.min_size,
                    max_candidates=config.ocr.max_candidates,
                    recognition_batch_size=config.ocr.recognition_batch_size,
                ),
                assets,
            )
        if component_id == "evidence.watermark":
            return WatermarkEvidenceRuntime(
                WatermarkEvidenceConfig(device=config.device, precision=config.precision),
                assets,
            )
        raise ValueError(f"No runtime factory for component {component_id}")

    @staticmethod
    def _clip_capabilities(config: ScoringConfig) -> tuple[str, ...]:
        capabilities = []
        if "aesthetic" in config.enabled_components:
            capabilities.append(AESTHETIC_FEATURE_CAPABILITY)
        if "ai" in config.enabled_components:
            capabilities.append(UFD_FEATURE_CAPABILITY)
        return tuple(sorted(capabilities))

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
    def _load_images(samples: tuple[SampleInput, ...]) -> tuple[Image.Image, ...]:
        images: list[Image.Image] = []
        try:
            for sample in samples:
                with Image.open(sample.image_path) as source:
                    source.load()
                    images.append(ImageOps.exif_transpose(source).copy())
            return tuple(images)
        except Exception:
            ModularScoringComponentService._close_images(tuple(images))
            raise

    @staticmethod
    def _close_images(images: tuple[Image.Image, ...]) -> None:
        for image in images:
            image.close()

    @classmethod
    def _require_finite(cls, value: Any, *, path: str = "result") -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise RuntimeError(f"Non-finite component output at {path}")
        if isinstance(value, dict):
            for key, item in value.items():
                cls._require_finite(item, path=f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                cls._require_finite(item, path=f"{path}[{index}]")

    @staticmethod
    def _summary(
        task_id: str,
        component_id: str,
        samples: tuple[SampleInput, ...],
        processed: int,
        inferred: int,
        cached: int,
        resumed_from: int,
        complete: bool,
        status: str,
    ) -> ModularScoringSummary:
        return ModularScoringSummary(
            task_id=task_id,
            component_id=component_id,
            eligible_samples=len(samples),
            processed_samples=processed,
            inferred_samples=inferred,
            cached_samples=cached,
            resumed_from_index=resumed_from,
            component_complete=complete,
            final_status=status,
        )


def finalize_modular_scoring(
    tasks: TaskService,
    token: WorkerToken,
    *,
    component_order: tuple[str, ...],
) -> str:
    task = tasks.get_task(token.task_id)
    checkpoints = [
        checkpoint
        for checkpoint in tasks.list_checkpoints(
            task.id,
            phase=TaskStatus.MODEL_SCORING.value,
        )
        if checkpoint.config_hash == task.config_hash
    ]
    completed = {
        str(checkpoint.cursor.get("component_id"))
        for checkpoint in checkpoints
        if checkpoint.cursor.get("modular_scoring") is True
        and checkpoint.cursor.get("component_complete") is True
    }
    missing = set(component_order) - completed
    if missing:
        raise RuntimeError(f"Cannot finalize incomplete scoring components: {sorted(missing)}")
    current = tasks.get_task(task.id)
    if current.status in {TaskStatus.PAUSING.value, TaskStatus.TERMINATING.value}:
        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        return tasks.commit_batch(
            token,
            phase=TaskStatus.MODEL_SCORING,
            config_hash=task.config_hash,
            batch_index=batch_index,
            completed_items=current.progress_current,
            progress_total=current.progress_total,
            cursor={
                "modular_scoring": True,
                "component_id": "scoring.finalize",
                "control_only": True,
            },
            lease_seconds=300,
        ).task.status
    try:
        return tasks.complete_phase(token, phase=TaskStatus.MODEL_SCORING).status
    except StaleWorkerToken:
        return tasks.get_task(task.id).status
    except InvalidTaskTransition:
        current = tasks.get_task(task.id)
        if current.status not in {TaskStatus.PAUSING.value, TaskStatus.TERMINATING.value}:
            raise
        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        return tasks.commit_batch(
            token,
            phase=TaskStatus.MODEL_SCORING,
            config_hash=task.config_hash,
            batch_index=batch_index,
            completed_items=current.progress_current,
            progress_total=current.progress_total,
            cursor={
                "modular_scoring": True,
                "component_id": "scoring.finalize",
                "control_only": True,
            },
            lease_seconds=300,
        ).task.status
