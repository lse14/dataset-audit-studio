from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from dataset_audit_studio.clustering.assets import (
    SIGLIP_MODEL_ID,
    SIGLIP_PREPROCESSING_VERSION,
)
from dataset_audit_studio.clustering.config import ClusteringConfig
from dataset_audit_studio.clustering.repository import (
    EMBEDDING_ARTIFACT_KIND,
    SAE_ARTIFACT_KIND,
    ClusteringRepository,
)
from dataset_audit_studio.clustering.sae_store import SAEStore
from dataset_audit_studio.clustering.shards import EmbeddingShardStore
from dataset_audit_studio.clustering.types import (
    ClusteringScope,
    EmbeddingSample,
    EmbeddingShard,
    SAEArtifact,
)
from dataset_audit_studio.components.cluster_hierarchy.algorithm import (
    ClusteringInterrupted,
    character_consistency_config_hash,
    character_consistency_config_payload,
    hierarchical_clusters,
)
from dataset_audit_studio.components.cluster_hierarchy.config import HierarchyConfig
from dataset_audit_studio.components.sae_analysis.config import SparseAutoencoderConfig
from dataset_audit_studio.components.sae_analysis.runtime import (
    SAEInterrupted,
    train_sparse_autoencoder,
)
from dataset_audit_studio.components.semantic_embedding.config import (
    SemanticEmbeddingConfig,
)
from dataset_audit_studio.components.semantic_embedding.runtime import (
    TorchEmbeddingRuntime,
)
from dataset_audit_studio.core.model_assets import RuntimeAssets, runtime_model_digest
from dataset_audit_studio.core.profile_contracts import DatasetProfile
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.jobs.errors import InvalidTaskTransition, StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT

EMBEDDING_COMPONENT_ID = "embedding.semantic"
SAE_COMPONENT_ID = "analysis.sae"
HIERARCHY_COMPONENT_ID = "cluster.hierarchy"
MODULAR_CLUSTERING_COMPONENT_IDS = frozenset(
    (EMBEDDING_COMPONENT_ID, SAE_COMPONENT_ID, HIERARCHY_COMPONENT_ID)
)

EmbeddingRuntimeFactory = Callable[[SemanticEmbeddingConfig, RuntimeAssets], Any]
SAETrainer = Callable[..., Any]


@dataclass(frozen=True)
class ModularClusteringSummary:
    task_id: str
    component_id: str
    eligible_samples: int
    processed_samples: int
    inferred_samples: int
    cached_samples: int
    output_count: int
    component_complete: bool
    final_status: str


class ModularClusteringComponentService:
    def __init__(
        self,
        tasks: TaskService,
        *,
        repository: ClusteringRepository | None = None,
        embedding_runtime_factory: EmbeddingRuntimeFactory | None = None,
        sae_trainer: SAETrainer = train_sparse_autoencoder,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.tasks = tasks
        self.project_root = project_root.resolve(strict=False)
        self.repository = repository or ClusteringRepository(project_root=project_root)
        self.embedding_runtime_factory = (
            embedding_runtime_factory or self._default_embedding_runtime
        )
        self.sae_trainer = sae_trainer
        self.shards = EmbeddingShardStore(project_root=project_root)
        self.sae_store = SAEStore(project_root=project_root)

    def run(
        self,
        token: WorkerToken,
        assets: RuntimeAssets,
        *,
        component_id: str,
        component_order: tuple[str, ...],
    ) -> ModularClusteringSummary:
        if component_id not in MODULAR_CLUSTERING_COMPONENT_IDS:
            raise ValueError(f"Unsupported modular clustering component: {component_id}")
        if component_id not in component_order or len(component_order) != len(
            set(component_order)
        ):
            raise ValueError("Clustering component order is invalid")
        control = self.tasks.honor_claimed_control_before_work(
            token,
            phase=TaskStatus.SEMANTIC_CLUSTERING,
        )
        if control is not None:
            return ModularClusteringSummary(
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
        config = ClusteringConfig.from_task_config(task.config)
        if not config.enabled:
            raise ValueError(f"Disabled clustering scheduled component {component_id}")
        if component_id == SAE_COMPONENT_ID and not config.sae.enabled:
            raise ValueError("Disabled SAE component was scheduled")
        with self.tasks.database.read_session() as session:
            samples = self.repository.list_samples(
                session,
                task,
                artist_core_only=config.scope_mode == "artist",
            )
        checkpoints = [
            checkpoint
            for checkpoint in self.tasks.list_checkpoints(
                task.id,
                phase=TaskStatus.SEMANTIC_CLUSTERING.value,
            )
            if checkpoint.config_hash == task.config_hash
        ]
        if component_id == EMBEDDING_COMPONENT_ID:
            return self._run_embedding(
                token, task, config, samples, checkpoints, assets, component_order
            )
        if component_id == SAE_COMPONENT_ID:
            return self._run_sae(
                token, task, config, samples, checkpoints, component_order
            )
        return self._run_hierarchy(
            token, task, config, samples, checkpoints, component_order
        )

    def _run_embedding(
        self,
        token,
        task,
        config: ClusteringConfig,
        samples: tuple[EmbeddingSample, ...],
        checkpoints,
        assets: RuntimeAssets,
        component_order: tuple[str, ...],
    ) -> ModularClusteringSummary:
        component_config = SemanticEmbeddingConfig(
            device=config.device,
            batch_size=config.embedding_batch_size,
            shard_size=config.embedding_shard_size,
        )
        model_digest = runtime_model_digest(assets, (SIGLIP_MODEL_ID,))
        sample_digest = self._sample_digest(samples)
        identity_digest = self._digest(
            {
                "component": EMBEDDING_COMPONENT_ID,
                "sample_digest": sample_digest,
                "model_digest": model_digest,
                "preprocessing": SIGLIP_PREPROCESSING_VERSION,
                "config": component_config.model_dump(mode="json"),
            }
        )
        last = self._last_component_checkpoint(checkpoints, EMBEDDING_COMPONENT_ID)
        self._validate_resume_identity(last, identity_digest, EMBEDDING_COMPONENT_ID)
        shard_ranges = tuple(
            (start, min(start + component_config.shard_size, len(samples)))
            for start in range(0, len(samples), component_config.shard_size)
        )
        next_shard = int(last.cursor.get("next_shard", 0)) if last else 0
        artifact_keys = list(last.cursor.get("artifact_keys", ())) if last else []
        inferred = int(last.cursor.get("inferred_samples", 0)) if last else 0
        cached = int(last.cursor.get("cached_samples", 0)) if last else 0
        if not 0 <= next_shard <= len(shard_ranges) or len(artifact_keys) != next_shard:
            raise ValueError("Semantic embedding checkpoint is inconsistent")
        if last is not None and last.cursor.get("component_complete") is True:
            return self._summary(
                task.id,
                EMBEDDING_COMPONENT_ID,
                samples,
                len(samples),
                inferred,
                cached,
                len(artifact_keys),
                True,
            )
        batch_index = self._next_batch_index(checkpoints)
        runtime = None
        processed = shard_ranges[next_shard][0] if next_shard < len(shard_ranges) else 0
        try:
            if not shard_ranges:
                cursor = self._embedding_cursor(
                    identity_digest,
                    sample_digest,
                    model_digest,
                    0,
                    artifact_keys,
                    inferred,
                    cached,
                    True,
                )
                result = self._commit(
                    token,
                    task,
                    checkpoints,
                    component_order,
                    EMBEDDING_COMPONENT_ID,
                    batch_index,
                    0,
                    len(samples),
                    cursor,
                )
                return self._summary(
                    task.id,
                    EMBEDDING_COMPONENT_ID,
                    samples,
                    0,
                    inferred,
                    cached,
                    0,
                    True,
                    result.task.status,
                )
            for shard_index in range(next_shard, len(shard_ranges)):
                start, end = shard_ranges[shard_index]
                shard_samples = samples[start:end]
                ids = tuple(sample.sample_id for sample in shard_samples)
                hashes = tuple(sample.pixel_sha256 for sample in shard_samples)
                key = self.shards.cache_key(
                    sample_ids=ids,
                    pixel_hashes=hashes,
                    model_sha256=model_digest,
                    preprocessing_version=SIGLIP_PREPROCESSING_VERSION,
                )
                shard = self._registered_shard(task.id, key)
                if shard is None:
                    matrices: list[np.ndarray] = []
                    if runtime is None:
                        runtime = self.embedding_runtime_factory(component_config, assets)
                        self.tasks.mark_phase_process_ready(
                            token,
                            phase=TaskStatus.SEMANTIC_CLUSTERING,
                            component_id=EMBEDDING_COMPONENT_ID,
                        )
                    for offset in range(0, len(shard_samples), component_config.batch_size):
                        batch_samples = shard_samples[offset : offset + component_config.batch_size]
                        self._verify_sources(batch_samples)
                        batch = runtime.embed(batch_samples)
                        if batch.sample_ids != tuple(
                            sample.sample_id for sample in batch_samples
                        ):
                            raise RuntimeError("Embedding runtime returned samples out of order")
                        matrices.append(np.asarray(batch.embeddings, dtype=np.float32))
                        if self._control_requested(task.id):
                            cursor = self._embedding_cursor(
                                identity_digest,
                                sample_digest,
                                model_digest,
                                shard_index,
                                artifact_keys,
                                inferred,
                                cached,
                                False,
                            )
                            result = self._commit(
                                token,
                                task,
                                checkpoints,
                                component_order,
                                EMBEDDING_COMPONENT_ID,
                                batch_index,
                                start,
                                len(samples),
                                {**cursor, "control_only": True},
                            )
                            return self._summary(
                                task.id,
                                EMBEDDING_COMPONENT_ID,
                                samples,
                                start,
                                inferred,
                                cached,
                                len(artifact_keys),
                                False,
                                result.task.status,
                            )
                    shard = self.shards.write(
                        task_id=task.id,
                        sample_ids=ids,
                        pixel_hashes=hashes,
                        embeddings=np.concatenate(matrices, axis=0),
                        model_sha256=model_digest,
                        preprocessing_version=SIGLIP_PREPROCESSING_VERSION,
                    )
                    inferred += len(shard_samples)
                else:
                    cached += len(shard_samples)
                artifact_keys.append(shard.cache_key)
                processed = end
                complete = shard_index + 1 == len(shard_ranges)
                cursor = self._embedding_cursor(
                    identity_digest,
                    sample_digest,
                    model_digest,
                    shard_index + 1,
                    artifact_keys,
                    inferred,
                    cached,
                    complete,
                )

                def register(session, *, current=shard) -> None:
                    self.repository.register_embedding_shard(
                        session,
                        task_id=task.id,
                        shard=current,
                    )

                result = self._commit(
                    token,
                    task,
                    checkpoints,
                    component_order,
                    EMBEDDING_COMPONENT_ID,
                    batch_index,
                    end,
                    len(samples),
                    cursor,
                    writer=register,
                )
                batch_index += 1
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        EMBEDDING_COMPONENT_ID,
                        samples,
                        processed,
                        inferred,
                        cached,
                        len(artifact_keys),
                        complete,
                        result.task.status,
                    )
            return self._summary(
                task.id,
                EMBEDDING_COMPONENT_ID,
                samples,
                processed,
                inferred,
                cached,
                len(artifact_keys),
                True,
            )
        finally:
            if runtime is not None:
                runtime.close()

    def _run_sae(
        self,
        token,
        task,
        config: ClusteringConfig,
        samples: tuple[EmbeddingSample, ...],
        checkpoints,
        component_order: tuple[str, ...],
    ) -> ModularClusteringSummary:
        shards, embedding_cursor = self._embedding_artifacts(task.id, samples, checkpoints)
        component_config = SparseAutoencoderConfig.model_validate(
            config.sae.model_dump(mode="python", exclude={"enabled"})
        )
        identity_digest = self._digest(
            {
                "component": SAE_COMPONENT_ID,
                "sample_digest": embedding_cursor["sample_digest"],
                "shards": [shard.sha256 for shard in shards],
                "config": component_config.model_dump(mode="json"),
            }
        )
        last = self._last_component_checkpoint(checkpoints, SAE_COMPONENT_ID)
        self._validate_resume_identity(last, identity_digest, SAE_COMPONENT_ID)
        if last is not None and last.cursor.get("component_complete") is True:
            return self._summary(
                task.id,
                SAE_COMPONENT_ID,
                samples,
                len(samples),
                0,
                len(samples) if last.cursor.get("cache_hit") else 0,
                int(last.cursor.get("feature_count", 0)),
                True,
            )
        cache_key = self.sae_store.cache_key(
            tuple(shard.sha256 for shard in shards),
            component_config,
        )
        artifact = self._registered_sae(task.id, cache_key)
        cache_hit = artifact is not None
        if artifact is None and samples:
            embeddings = self._load_embeddings(samples, shards)
            try:
                analysis = self.sae_trainer(
                    embeddings,
                    component_config,
                    device=self._sae_device(config),
                    should_stop=lambda: self._control_requested(task.id),
                )
            except SAEInterrupted:
                cursor = self._component_cursor(
                    SAE_COMPONENT_ID,
                    identity_digest,
                    component_complete=False,
                    sae_cache_key=None,
                    feature_count=0,
                    cache_hit=False,
                )
                result = self._commit(
                    token,
                    task,
                    checkpoints,
                    component_order,
                    SAE_COMPONENT_ID,
                    self._next_batch_index(checkpoints),
                    0,
                    len(samples),
                    {**cursor, "control_only": True},
                )
                return self._summary(
                    task.id,
                    SAE_COMPONENT_ID,
                    samples,
                    0,
                    0,
                    0,
                    0,
                    False,
                    result.task.status,
                )
            artifact = self.sae_store.write(
                task_id=task.id,
                cache_key=cache_key,
                sample_ids=tuple(sample.sample_id for sample in samples),
                analysis=analysis,
            )
        cursor = self._component_cursor(
            SAE_COMPONENT_ID,
            identity_digest,
            component_complete=True,
            sae_cache_key=artifact.cache_key if artifact is not None else None,
            feature_count=artifact.feature_count if artifact is not None else 0,
            cache_hit=cache_hit,
        )

        def register(session) -> None:
            if artifact is not None:
                self.repository.register_sae(session, task_id=task.id, artifact=artifact)

        result = self._commit(
            token,
            task,
            checkpoints,
            component_order,
            SAE_COMPONENT_ID,
            self._next_batch_index(checkpoints),
            len(samples),
            len(samples),
            cursor,
            writer=register,
        )
        return self._summary(
            task.id,
            SAE_COMPONENT_ID,
            samples,
            len(samples),
            len(samples) if artifact is not None and not cache_hit else 0,
            len(samples) if cache_hit else 0,
            artifact.feature_count if artifact is not None else 0,
            True,
            result.task.status,
        )

    def _run_hierarchy(
        self,
        token,
        task,
        config: ClusteringConfig,
        samples: tuple[EmbeddingSample, ...],
        checkpoints,
        component_order: tuple[str, ...],
    ) -> ModularClusteringSummary:
        shards, embedding_cursor = self._embedding_artifacts(task.id, samples, checkpoints)
        component_config = HierarchyConfig(
            scope_mode=config.scope_mode,
            minimum_split_size=config.minimum_split_size,
            target_leaf_size=config.target_leaf_size,
            max_branching=config.max_branching,
            kmeans_iterations=config.kmeans_iterations,
            seed=config.seed,
        )
        character_consistency_enabled = (
            task.config.get("profile") == DatasetProfile.CHARACTER_CONCEPT.value
        )
        hierarchy_payload = component_config.model_dump(mode="json")
        character_config_hash = None
        if character_consistency_enabled:
            hierarchy_payload["character_consistency"] = (
                character_consistency_config_payload()
            )
            character_config_hash = character_consistency_config_hash()
        hierarchy_hash = self._digest(hierarchy_payload)
        character_consistency = (
            self.repository.character_consistency_metadata(shards)
            if character_consistency_enabled and shards
            else None
        )
        identity_digest = self._digest(
            {
                "component": HIERARCHY_COMPONENT_ID,
                "sample_digest": embedding_cursor["sample_digest"],
                "shards": [shard.sha256 for shard in shards],
                "hierarchy_hash": hierarchy_hash,
                "character_consistency_config_hash": character_config_hash,
            }
        )
        last = self._last_component_checkpoint(checkpoints, HIERARCHY_COMPONENT_ID)
        self._validate_resume_identity(last, identity_digest, HIERARCHY_COMPONENT_ID)
        scopes = self.repository.scopes(
            samples,
            artist_mode=component_config.scope_mode in {"artist", "concept"},
        )
        next_scope = int(last.cursor.get("next_scope", 0)) if last else 0
        prepared = bool(last and last.cursor.get("clusters_prepared"))
        node_count = int(last.cursor.get("cluster_nodes", 0)) if last else 0
        if not 0 <= next_scope <= len(scopes):
            raise ValueError("Hierarchy checkpoint scope is invalid")
        if last is not None and last.cursor.get("component_complete") is True:
            return self._summary(
                task.id,
                HIERARCHY_COMPONENT_ID,
                samples,
                len(samples),
                0,
                0,
                node_count,
                True,
            )
        embeddings = self._load_embeddings(samples, shards)
        sae_cache_key = self._sae_cache_key(checkpoints, required=config.sae.enabled)
        batch_index = self._next_batch_index(checkpoints)
        completed = sum(len(scope.sample_indices) for scope in scopes[:next_scope])
        if not scopes:
            cursor = self._hierarchy_cursor(
                identity_digest,
                hierarchy_hash,
                embedding_cursor,
                sae_cache_key,
                0,
                0,
                True,
                True,
                character_config_hash,
            )

            def prepare_empty(session) -> None:
                self.repository.prepare_empty_clusters(session, task.id)

            result = self._commit(
                token,
                task,
                checkpoints,
                component_order,
                HIERARCHY_COMPONENT_ID,
                batch_index,
                0,
                len(samples),
                cursor,
                writer=prepare_empty,
            )
            return self._summary(
                task.id,
                HIERARCHY_COMPONENT_ID,
                samples,
                0,
                0,
                0,
                0,
                True,
                result.task.status,
            )
        for scope_index in range(next_scope, len(scopes)):
            scope = scopes[scope_index]
            local_embeddings = embeddings[list(scope.sample_indices)]
            local_keys = tuple(samples[index].relative_path for index in scope.sample_indices)
            try:
                nodes = hierarchical_clusters(
                    local_embeddings,
                    local_keys,
                    scope_kind=component_config.scope_mode,
                    scope_id=scope.scope_id,
                    config=component_config,
                    should_stop=lambda: self._control_requested(task.id),
                )
            except ClusteringInterrupted:
                cursor = self._hierarchy_cursor(
                    identity_digest,
                    hierarchy_hash,
                    embedding_cursor,
                    sae_cache_key,
                    scope_index,
                    node_count,
                    prepared,
                    False,
                    character_config_hash,
                )
                result = self._commit(
                    token,
                    task,
                    checkpoints,
                    component_order,
                    HIERARCHY_COMPONENT_ID,
                    batch_index,
                    completed,
                    len(samples),
                    {**cursor, "control_only": True},
                )
                return self._summary(
                    task.id,
                    HIERARCHY_COMPONENT_ID,
                    samples,
                    completed,
                    0,
                    0,
                    node_count,
                    False,
                    result.task.status,
                )
            completed += len(scope.sample_indices)
            node_count += len(nodes)
            complete = scope_index + 1 == len(scopes)
            cursor = self._hierarchy_cursor(
                identity_digest,
                hierarchy_hash,
                embedding_cursor,
                sae_cache_key,
                scope_index + 1,
                node_count,
                True,
                complete,
                character_config_hash,
            )

            def persist_scope(
                session,
                *,
                current_scope: ClusteringScope = scope,
                current_nodes=nodes,
                current_embeddings=local_embeddings,
                prepare=not prepared,
            ) -> None:
                self.repository.persist_cluster_scope(
                    session,
                    task_id=task.id,
                    scope=current_scope,
                    nodes=current_nodes,
                    samples=samples,
                    scope_embeddings=current_embeddings,
                    hierarchy_config_hash=hierarchy_hash,
                    prepare=prepare,
                    character_consistency=character_consistency,
                )

            result = self._commit(
                token,
                task,
                checkpoints,
                component_order,
                HIERARCHY_COMPONENT_ID,
                batch_index,
                completed,
                len(samples),
                cursor,
                writer=persist_scope,
            )
            batch_index += 1
            prepared = True
            if result.control_state != "continue":
                return self._summary(
                    task.id,
                    HIERARCHY_COMPONENT_ID,
                    samples,
                    completed,
                    0,
                    0,
                    node_count,
                    complete,
                    result.task.status,
                )
        return self._summary(
            task.id,
            HIERARCHY_COMPONENT_ID,
            samples,
            completed,
            0,
            0,
            node_count,
            True,
        )

    def _embedding_artifacts(self, task_id, samples, checkpoints):
        checkpoint = self._last_component_checkpoint(checkpoints, EMBEDDING_COMPONENT_ID)
        if checkpoint is None or checkpoint.cursor.get("component_complete") is not True:
            raise RuntimeError("Semantic embedding component is incomplete")
        if checkpoint.cursor.get("sample_digest") != self._sample_digest(samples):
            raise RuntimeError("Semantic embedding sample identity changed")
        shards = [
            self._registered_shard(task_id, key)
            for key in checkpoint.cursor.get("artifact_keys", ())
        ]
        if any(shard is None for shard in shards):
            raise RuntimeError("Semantic embedding artifact is missing")
        return [shard for shard in shards if shard is not None], checkpoint.cursor

    def _registered_shard(self, task_id: str, cache_key: str) -> EmbeddingShard | None:
        with self.tasks.database.read_session() as session:
            snapshot = self.repository.artifact_snapshot(
                session,
                task_id=task_id,
                kind=EMBEDDING_ARTIFACT_KIND,
                cache_key=cache_key,
            )
        if snapshot is None:
            return None
        shard = self.shards.inspect(task_id=task_id, cache_key=cache_key)
        if (
            snapshot["state"] != "ready"
            or snapshot["path"] != shard.relative_path
            or snapshot["sha256"] != shard.sha256
            or snapshot["size_bytes"] != shard.size_bytes
        ):
            raise RuntimeError("Registered semantic embedding artifact changed on disk")
        return shard

    def _registered_sae(self, task_id: str, cache_key: str) -> SAEArtifact | None:
        with self.tasks.database.read_session() as session:
            snapshot = self.repository.artifact_snapshot(
                session,
                task_id=task_id,
                kind=SAE_ARTIFACT_KIND,
                cache_key=cache_key,
            )
        if snapshot is None:
            return None
        artifact = self.sae_store.inspect(task_id=task_id, cache_key=cache_key)
        if (
            snapshot["state"] != "ready"
            or snapshot["path"] != artifact.relative_path
            or snapshot["sha256"] != artifact.sha256
            or snapshot["size_bytes"] != artifact.size_bytes
        ):
            raise RuntimeError("Registered SAE artifact changed on disk")
        return artifact

    def _load_embeddings(
        self,
        samples: tuple[EmbeddingSample, ...],
        shards: list[EmbeddingShard],
    ) -> np.ndarray:
        if not samples:
            return np.empty((0, 0), dtype=np.float32)
        sample_ids = tuple(sample_id for shard in shards for sample_id in shard.sample_ids)
        if sample_ids != tuple(sample.sample_id for sample in samples):
            raise RuntimeError("Embedding shard order does not match component samples")
        matrices = [self.shards.load(shard) for shard in shards]
        if len({matrix.shape[1] for matrix in matrices}) != 1:
            raise RuntimeError("Embedding shard dimensions do not match")
        return np.concatenate(matrices, axis=0)

    @staticmethod
    def _last_component_checkpoint(checkpoints, component_id):
        values = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.cursor.get("modular_clustering") is True
            and checkpoint.cursor.get("component_id") == component_id
        ]
        return values[-1] if values else None

    @staticmethod
    def _validate_resume_identity(last, identity_digest: str, component_id: str) -> None:
        if last is not None and last.cursor.get("identity_digest") != identity_digest:
            raise ValueError(f"{component_id} identity changed while resuming")

    @staticmethod
    def _next_batch_index(checkpoints) -> int:
        return checkpoints[-1].batch_index + 1 if checkpoints else 0

    def _commit(
        self,
        token,
        task,
        checkpoints,
        component_order,
        component_id,
        batch_index,
        local_completed,
        sample_count,
        cursor,
        *,
        writer=None,
    ):
        component_index = component_order.index(component_id)
        logical = component_index * sample_count + local_completed
        current = self.tasks.get_task(task.id)
        completed = max(current.progress_current, logical)
        total = max(completed, sample_count * len(component_order))
        return self.tasks.commit_batch(
            token,
            phase=TaskStatus.SEMANTIC_CLUSTERING,
            config_hash=task.config_hash,
            batch_index=batch_index,
            completed_items=completed,
            progress_total=total,
            cursor=cursor,
            lease_seconds=300,
            batch_writer=writer,
        )

    @staticmethod
    def _component_cursor(component_id, identity_digest, *, component_complete, **values):
        return {
            "modular_clustering": True,
            "component_id": component_id,
            "identity_digest": identity_digest,
            "component_complete": component_complete,
            **values,
        }

    def _embedding_cursor(
        self,
        identity_digest,
        sample_digest,
        model_digest,
        next_shard,
        artifact_keys,
        inferred,
        cached,
        complete,
    ):
        return self._component_cursor(
            EMBEDDING_COMPONENT_ID,
            identity_digest,
            component_complete=complete,
            sample_digest=sample_digest,
            embedding_model_digest=model_digest,
            preprocessing_version=SIGLIP_PREPROCESSING_VERSION,
            next_shard=next_shard,
            artifact_keys=list(artifact_keys),
            inferred_samples=inferred,
            cached_samples=cached,
        )

    def _hierarchy_cursor(
        self,
        identity_digest,
        hierarchy_hash,
        embedding_cursor,
        sae_cache_key,
        next_scope,
        node_count,
        prepared,
        complete,
        character_config_hash,
    ):
        return self._component_cursor(
            HIERARCHY_COMPONENT_ID,
            identity_digest,
            component_complete=complete,
            sample_digest=embedding_cursor["sample_digest"],
            artifact_keys=list(embedding_cursor["artifact_keys"]),
            sae_cache_key=sae_cache_key,
            hierarchy_hash=hierarchy_hash,
            next_scope=next_scope,
            cluster_nodes=node_count,
            clusters_prepared=prepared,
            character_consistency_config_hash=character_config_hash,
        )

    @classmethod
    def _sae_cache_key(cls, checkpoints, *, required: bool) -> str | None:
        checkpoint = cls._last_component_checkpoint(checkpoints, SAE_COMPONENT_ID)
        if checkpoint is None:
            if required:
                raise RuntimeError("SAE component is incomplete")
            return None
        if checkpoint.cursor.get("component_complete") is not True:
            raise RuntimeError("SAE component is incomplete")
        value = checkpoint.cursor.get("sae_cache_key")
        return str(value) if value is not None else None

    @staticmethod
    def _sample_digest(samples: tuple[EmbeddingSample, ...]) -> str:
        return ModularClusteringComponentService._digest(
            [
                [sample.sample_id, sample.pixel_sha256, sample.artist_scope]
                for sample in samples
            ]
        )

    @staticmethod
    def _digest(payload: Any) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _verify_sources(samples: tuple[EmbeddingSample, ...]) -> None:
        for sample in samples:
            stat = sample.source_path.stat()
            if stat.st_size != sample.source_size or stat.st_mtime_ns != sample.source_mtime_ns:
                raise RuntimeError(
                    f"Source changed after scanning: {sample.relative_path}; rescan required"
                )
            if not sample.image_path.is_file():
                raise RuntimeError(f"Embedding image is missing: {sample.relative_path}")

    def _control_requested(self, task_id: str) -> bool:
        return self.tasks.get_task(task_id).status in {
            TaskStatus.PAUSING.value,
            TaskStatus.TERMINATING.value,
        }

    @staticmethod
    def _sae_device(config: ClusteringConfig) -> str:
        if config.device == "cpu":
            return "cpu"
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    @staticmethod
    def _default_embedding_runtime(config, assets):
        return TorchEmbeddingRuntime(config, assets)

    def _summary(
        self,
        task_id,
        component_id,
        samples,
        processed,
        inferred,
        cached,
        output_count,
        complete,
        status=None,
    ):
        return ModularClusteringSummary(
            task_id=task_id,
            component_id=component_id,
            eligible_samples=len(samples),
            processed_samples=processed,
            inferred_samples=inferred,
            cached_samples=cached,
            output_count=output_count,
            component_complete=complete,
            final_status=status or self.tasks.get_task(task_id).status,
        )


def finalize_modular_clustering(
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
            phase=TaskStatus.SEMANTIC_CLUSTERING.value,
        )
        if checkpoint.config_hash == task.config_hash
    ]
    completed = {
        checkpoint.cursor.get("component_id")
        for checkpoint in checkpoints
        if checkpoint.cursor.get("modular_clustering") is True
        and checkpoint.cursor.get("component_complete") is True
    }
    missing = set(component_order) - completed
    if missing:
        raise RuntimeError(f"Cannot finalize incomplete clustering components: {sorted(missing)}")
    current = tasks.get_task(task.id)
    if current.status in {TaskStatus.PAUSING.value, TaskStatus.TERMINATING.value}:
        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        return tasks.commit_batch(
            token,
            phase=TaskStatus.SEMANTIC_CLUSTERING,
            config_hash=task.config_hash,
            batch_index=batch_index,
            completed_items=current.progress_current,
            progress_total=max(current.progress_current, current.progress_total or 0),
            cursor={
                "modular_clustering": True,
                "component_id": "clustering.finalize",
                "control_only": True,
            },
            lease_seconds=300,
        ).task.status
    try:
        return tasks.complete_phase(token, phase=TaskStatus.SEMANTIC_CLUSTERING).status
    except StaleWorkerToken:
        return tasks.get_task(task.id).status
    except InvalidTaskTransition:
        current = tasks.get_task(task.id)
        if current.status not in {TaskStatus.PAUSING.value, TaskStatus.TERMINATING.value}:
            raise
        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        return tasks.commit_batch(
            token,
            phase=TaskStatus.SEMANTIC_CLUSTERING,
            config_hash=task.config_hash,
            batch_index=batch_index,
            completed_items=current.progress_current,
            progress_total=max(current.progress_current, current.progress_total or 0),
            cursor={
                "modular_clustering": True,
                "component_id": "clustering.finalize",
                "control_only": True,
            },
            lease_seconds=300,
        ).task.status
