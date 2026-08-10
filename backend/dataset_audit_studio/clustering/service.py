from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dataset_audit_studio.clustering.assets import (
    SIGLIP_PREPROCESSING_VERSION,
    embedding_model_sha256,
)
from dataset_audit_studio.clustering.config import ClusteringConfig
from dataset_audit_studio.clustering.hierarchy import (
    ClusteringInterrupted,
    hierarchical_clusters,
)
from dataset_audit_studio.clustering.repository import (
    EMBEDDING_ARTIFACT_KIND,
    SAE_ARTIFACT_KIND,
    ClusteringRepository,
)
from dataset_audit_studio.clustering.sae import (
    SAEInterrupted,
    train_sparse_autoencoder,
)
from dataset_audit_studio.clustering.sae_store import SAEStore
from dataset_audit_studio.clustering.shards import EmbeddingShardStore
from dataset_audit_studio.clustering.types import (
    ClusteringScope,
    EmbeddingRuntime,
    EmbeddingSample,
    EmbeddingShard,
    SAEArtifact,
)
from dataset_audit_studio.components.cluster_hierarchy.algorithm import (
    character_consistency_config_payload,
)
from dataset_audit_studio.core.profile_contracts import DatasetProfile
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.jobs.errors import InvalidTaskTransition, StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scoring.types import RuntimeAssets

RuntimeFactory = Callable[[ClusteringConfig, RuntimeAssets], EmbeddingRuntime]


@dataclass(frozen=True)
class ClusteringSummary:
    task_id: str
    eligible_samples: int
    embedding_shards: int
    inferred_samples: int
    cached_samples: int
    cluster_scopes: int
    cluster_nodes: int
    sae_features: int
    final_status: str


class SemanticClusterer:
    def __init__(
        self,
        tasks: TaskService,
        *,
        repository: ClusteringRepository | None = None,
        runtime_factory: RuntimeFactory | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.tasks = tasks
        self.project_root = project_root.resolve(strict=False)
        self.repository = repository or ClusteringRepository(project_root=project_root)
        self.runtime_factory = runtime_factory or self._default_runtime_factory
        self.shards = EmbeddingShardStore(project_root=project_root)
        self.sae_store = SAEStore(project_root=project_root)

    def run(self, token: WorkerToken, assets: RuntimeAssets) -> ClusteringSummary:
        control = self.tasks.honor_claimed_control_before_work(
            token,
            phase=TaskStatus.SEMANTIC_CLUSTERING,
        )
        if control is not None:
            return ClusteringSummary(token.task_id, 0, 0, 0, 0, 0, 0, 0, control.status)
        task = self.tasks.get_task(token.task_id)
        config = ClusteringConfig.from_task_config(task.config)
        model_sha256 = (
            embedding_model_sha256(assets)
            if config.enabled
            else hashlib.sha256(b"clustering-disabled").hexdigest()
        )
        embedding_identity = hashlib.sha256(
            f"{model_sha256}\0{SIGLIP_PREPROCESSING_VERSION}".encode()
        ).hexdigest()
        character_consistency_enabled = (
            task.config.get("profile") == DatasetProfile.CHARACTER_CONCEPT.value
        )
        hierarchy_payload = config.hierarchy_payload()
        if character_consistency_enabled:
            hierarchy_payload = {
                "hierarchy": hierarchy_payload,
                "character_consistency": character_consistency_config_payload(),
            }
        hierarchy_hash = hashlib.sha256(
            json.dumps(
                hierarchy_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self.tasks.database.read_session() as session:
            samples = (
                self.repository.list_samples(
                    session,
                    task,
                    artist_core_only=config.scope_mode == "artist",
                )
                if config.enabled
                else ()
            )
        sample_digest = self._sample_digest(samples)
        scopes = self.repository.scopes(
            samples,
            artist_mode=config.scope_mode in {"artist", "concept"},
        )
        shard_ranges = tuple(
            (start, min(start + config.embedding_shard_size, len(samples)))
            for start in range(0, len(samples), config.embedding_shard_size)
        )
        checkpoints = [
            checkpoint
            for checkpoint in self.tasks.list_checkpoints(
                task.id, phase=TaskStatus.SEMANTIC_CLUSTERING.value
            )
            if checkpoint.config_hash == task.config_hash
        ]
        work_checkpoints = [
            checkpoint
            for checkpoint in checkpoints
            if checkpoint.cursor.get("asset_wait") is not True
        ]
        last_work = work_checkpoints[-1] if work_checkpoints else None
        if last_work is not None:
            if last_work.cursor.get("embedding_identity") != embedding_identity:
                raise ValueError("Embedding model identity changed since the checkpoint")
            if last_work.cursor.get("sample_digest") != sample_digest:
                raise ValueError("Clustering sample membership changed; restart the phase")
            if last_work.cursor.get("hierarchy_hash") != hierarchy_hash:
                raise ValueError("Clustering hierarchy config changed since the checkpoint")
        next_shard = int(last_work.cursor.get("next_shard", 0)) if last_work else 0
        next_scope = int(last_work.cursor.get("next_scope", 0)) if last_work else 0
        artifact_keys = list(last_work.cursor.get("artifact_keys", [])) if last_work else []
        sae_cache_key = last_work.cursor.get("sae_cache_key") if last_work else None
        clusters_prepared = bool(last_work and last_work.cursor.get("clusters_prepared"))
        inferred = int(last_work.cursor.get("inferred_samples", 0)) if last_work else 0
        cached = int(last_work.cursor.get("cached_samples", 0)) if last_work else 0
        if not 0 <= next_shard <= len(shard_ranges) or not 0 <= next_scope <= len(scopes):
            raise ValueError("Clustering checkpoint cursor is outside the current work list")
        if len(artifact_keys) != next_shard:
            raise ValueError("Clustering checkpoint artifact list is inconsistent")
        batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        runtime: EmbeddingRuntime | None = None
        registered_shards: list[EmbeddingShard] = []
        for key in artifact_keys:
            shard = self._registered_shard(task.id, key)
            if shard is None:
                raise RuntimeError("Registered embedding checkpoint artifact is missing")
            registered_shards.append(shard)
        total_progress = len(samples) * 2

        try:
            for shard_index in range(next_shard, len(shard_ranges)):
                start, end = shard_ranges[shard_index]
                shard_samples = samples[start:end]
                ids = tuple(sample.sample_id for sample in shard_samples)
                hashes = tuple(sample.pixel_sha256 for sample in shard_samples)
                key = self.shards.cache_key(
                    sample_ids=ids,
                    pixel_hashes=hashes,
                    model_sha256=model_sha256,
                    preprocessing_version=SIGLIP_PREPROCESSING_VERSION,
                )
                shard = self._registered_shard(task.id, key)
                if shard is not None:
                    cached += len(shard_samples)
                else:
                    if runtime is None:
                        runtime = self.runtime_factory(config, assets)
                    matrices: list[np.ndarray] = []
                    for offset in range(0, len(shard_samples), config.embedding_batch_size):
                        batch_samples = shard_samples[
                            offset : offset + config.embedding_batch_size
                        ]
                        self._verify_sources(batch_samples)
                        batch = runtime.embed(batch_samples)
                        if batch.sample_ids != tuple(
                            sample.sample_id for sample in batch_samples
                        ):
                            raise RuntimeError(
                                "Embedding runtime returned samples out of order"
                            )
                        matrices.append(np.asarray(batch.embeddings, dtype=np.float32))
                        if self._control_requested(task.id):
                            status = self._commit_control(
                                token,
                                task.config_hash,
                                batch_index,
                                start,
                                total_progress,
                                self._cursor(
                                    next_shard=shard_index,
                                    next_scope=0,
                                    artifact_keys=artifact_keys,
                                    sae_cache_key=None,
                                    embedding_identity=embedding_identity,
                                    sample_digest=sample_digest,
                                    hierarchy_hash=hierarchy_hash,
                                    inferred=inferred,
                                    cached=cached,
                                    clusters_prepared=False,
                                ),
                            )
                            return self._summary(
                                task.id,
                                samples,
                                registered_shards,
                                inferred,
                                cached,
                                scopes,
                                0,
                                None,
                                status,
                            )
                    matrix = np.concatenate(matrices, axis=0)
                    shard = self.shards.write(
                        task_id=task.id,
                        sample_ids=ids,
                        pixel_hashes=hashes,
                        embeddings=matrix,
                        model_sha256=model_sha256,
                        preprocessing_version=SIGLIP_PREPROCESSING_VERSION,
                    )
                    inferred += len(shard_samples)
                artifact_keys.append(shard.cache_key)
                registered_shards.append(shard)
                cursor = self._cursor(
                    next_shard=shard_index + 1,
                    next_scope=0,
                    artifact_keys=artifact_keys,
                    sae_cache_key=None,
                    embedding_identity=embedding_identity,
                    sample_digest=sample_digest,
                    hierarchy_hash=hierarchy_hash,
                    inferred=inferred,
                    cached=cached,
                    clusters_prepared=False,
                )

                def register_shard(session, *, current=shard) -> None:
                    self.repository.register_embedding_shard(
                        session,
                        task_id=task.id,
                        shard=current,
                    )

                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.SEMANTIC_CLUSTERING,
                    config_hash=task.config_hash,
                    batch_index=batch_index,
                    completed_items=end,
                    progress_total=total_progress,
                    cursor=cursor,
                    lease_seconds=300,
                    batch_writer=register_shard,
                )
                batch_index += 1
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        samples,
                        registered_shards,
                        inferred,
                        cached,
                        scopes,
                        0,
                        None,
                        result.task.status,
                    )

            if runtime is not None:
                runtime.close()
                runtime = None
            embeddings = self._load_embeddings(samples, registered_shards)
            character_consistency = (
                self.repository.character_consistency_metadata(registered_shards)
                if character_consistency_enabled and registered_shards
                else None
            )
            sae_artifact: SAEArtifact | None = None
            if config.sae.enabled and len(samples):
                expected_sae_key = self.sae_store.cache_key(
                    tuple(shard.sha256 for shard in registered_shards),
                    config.sae,
                )
                if sae_cache_key is not None and sae_cache_key != expected_sae_key:
                    raise ValueError("SAE checkpoint identity changed")
                sae_artifact = self._registered_sae(task.id, expected_sae_key)
                if sae_artifact is None:
                    try:
                        analysis = train_sparse_autoencoder(
                            embeddings,
                            config.sae,
                            device=self._sae_device(config),
                            should_stop=lambda: self._control_requested(task.id),
                        )
                    except SAEInterrupted:
                        status = self._commit_control(
                            token,
                            task.config_hash,
                            batch_index,
                            len(samples),
                            total_progress,
                            self._cursor(
                                next_shard=len(shard_ranges),
                                next_scope=0,
                                artifact_keys=artifact_keys,
                                sae_cache_key=None,
                                embedding_identity=embedding_identity,
                                sample_digest=sample_digest,
                                hierarchy_hash=hierarchy_hash,
                                inferred=inferred,
                                cached=cached,
                                clusters_prepared=False,
                            ),
                        )
                        return self._summary(
                            task.id,
                            samples,
                            registered_shards,
                            inferred,
                            cached,
                            scopes,
                            0,
                            None,
                            status,
                        )
                    sae_artifact = self.sae_store.write(
                        task_id=task.id,
                        cache_key=expected_sae_key,
                        sample_ids=tuple(sample.sample_id for sample in samples),
                        analysis=analysis,
                    )
                sae_cache_key = sae_artifact.cache_key
                cursor = self._cursor(
                    next_shard=len(shard_ranges),
                    next_scope=0,
                    artifact_keys=artifact_keys,
                    sae_cache_key=sae_cache_key,
                    embedding_identity=embedding_identity,
                    sample_digest=sample_digest,
                    hierarchy_hash=hierarchy_hash,
                    inferred=inferred,
                    cached=cached,
                    clusters_prepared=False,
                )

                def register_sae(session, *, current=sae_artifact) -> None:
                    assert current is not None
                    self.repository.register_sae(
                        session,
                        task_id=task.id,
                        artifact=current,
                    )

                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.SEMANTIC_CLUSTERING,
                    config_hash=task.config_hash,
                    batch_index=batch_index,
                    completed_items=len(samples),
                    progress_total=total_progress,
                    cursor=cursor,
                    lease_seconds=300,
                    batch_writer=register_sae,
                )
                batch_index += 1
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        samples,
                        registered_shards,
                        inferred,
                        cached,
                        scopes,
                        0,
                        sae_artifact,
                        result.task.status,
                    )

            if clusters_prepared:
                with self.tasks.database.read_session() as session:
                    node_count = self.repository.cluster_node_count(session, task.id)
            else:
                node_count = 0
            if not scopes and not clusters_prepared:
                cursor = self._cursor(
                    next_shard=len(shard_ranges),
                    next_scope=0,
                    artifact_keys=artifact_keys,
                    sae_cache_key=sae_cache_key,
                    embedding_identity=embedding_identity,
                    sample_digest=sample_digest,
                    hierarchy_hash=hierarchy_hash,
                    inferred=inferred,
                    cached=cached,
                    clusters_prepared=True,
                )

                def prepare_empty(session) -> None:
                    self.repository.prepare_empty_clusters(session, task.id)

                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.SEMANTIC_CLUSTERING,
                    config_hash=task.config_hash,
                    batch_index=batch_index,
                    completed_items=len(samples),
                    progress_total=total_progress,
                    cursor=cursor,
                    lease_seconds=300,
                    batch_writer=prepare_empty,
                )
                batch_index += 1
                clusters_prepared = True
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        samples,
                        registered_shards,
                        inferred,
                        cached,
                        scopes,
                        0,
                        sae_artifact,
                        result.task.status,
                    )

            completed_cluster_samples = sum(
                len(scope.sample_indices) for scope in scopes[:next_scope]
            )
            for scope_index in range(next_scope, len(scopes)):
                scope = scopes[scope_index]
                local_embeddings = embeddings[list(scope.sample_indices)]
                local_keys = tuple(samples[index].relative_path for index in scope.sample_indices)
                try:
                    nodes = hierarchical_clusters(
                        local_embeddings,
                        local_keys,
                        scope_kind=config.scope_mode,
                        scope_id=scope.scope_id,
                        config=config,
                        should_stop=lambda: self._control_requested(task.id),
                    )
                except ClusteringInterrupted:
                    status = self._commit_control(
                        token,
                        task.config_hash,
                        batch_index,
                        len(samples) + completed_cluster_samples,
                        total_progress,
                        self._cursor(
                            next_shard=len(shard_ranges),
                            next_scope=scope_index,
                            artifact_keys=artifact_keys,
                            sae_cache_key=sae_cache_key,
                            embedding_identity=embedding_identity,
                            sample_digest=sample_digest,
                            hierarchy_hash=hierarchy_hash,
                            inferred=inferred,
                            cached=cached,
                            clusters_prepared=clusters_prepared,
                        ),
                    )
                    return self._summary(
                        task.id,
                        samples,
                        registered_shards,
                        inferred,
                        cached,
                        scopes,
                        node_count,
                        sae_artifact,
                        status,
                    )
                completed_cluster_samples += len(scope.sample_indices)
                node_count += len(nodes)
                cursor = self._cursor(
                    next_shard=len(shard_ranges),
                    next_scope=scope_index + 1,
                    artifact_keys=artifact_keys,
                    sae_cache_key=sae_cache_key,
                    embedding_identity=embedding_identity,
                    sample_digest=sample_digest,
                    hierarchy_hash=hierarchy_hash,
                    inferred=inferred,
                    cached=cached,
                    clusters_prepared=True,
                )

                def persist_scope(
                    session,
                    *,
                    current_scope=scope,
                    current_nodes=nodes,
                    current_embeddings=local_embeddings,
                    prepare=not clusters_prepared,
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

                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.SEMANTIC_CLUSTERING,
                    config_hash=task.config_hash,
                    batch_index=batch_index,
                    completed_items=len(samples) + completed_cluster_samples,
                    progress_total=total_progress,
                    cursor=cursor,
                    lease_seconds=300,
                    batch_writer=persist_scope,
                )
                batch_index += 1
                clusters_prepared = True
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        samples,
                        registered_shards,
                        inferred,
                        cached,
                        scopes,
                        node_count,
                        sae_artifact,
                        result.task.status,
                    )
            status = self._complete_or_control(
                token,
                task.config_hash,
                batch_index,
                total_progress,
                self._cursor(
                    next_shard=len(shard_ranges),
                    next_scope=len(scopes),
                    artifact_keys=artifact_keys,
                    sae_cache_key=sae_cache_key,
                    embedding_identity=embedding_identity,
                    sample_digest=sample_digest,
                    hierarchy_hash=hierarchy_hash,
                    inferred=inferred,
                    cached=cached,
                    clusters_prepared=True,
                ),
            )
            return self._summary(
                task.id,
                samples,
                registered_shards,
                inferred,
                cached,
                scopes,
                node_count,
                sae_artifact,
                status,
            )
        finally:
            if runtime is not None:
                runtime.close()

    @staticmethod
    def _default_runtime_factory(
        config: ClusteringConfig, assets: RuntimeAssets
    ) -> EmbeddingRuntime:
        from dataset_audit_studio.clustering.torch_runtime import TorchEmbeddingRuntime

        return TorchEmbeddingRuntime(config, assets)

    def _load_embeddings(
        self,
        samples: tuple[EmbeddingSample, ...],
        shards: list[EmbeddingShard],
    ) -> np.ndarray:
        if not samples:
            return np.empty((0, 0), dtype=np.float32)
        if tuple(sample_id for shard in shards for sample_id in shard.sample_ids) != tuple(
            sample.sample_id for sample in samples
        ):
            raise RuntimeError("Embedding shard order does not match clustering samples")
        matrices = [self.shards.load(shard) for shard in shards]
        dimensions = {matrix.shape[1] for matrix in matrices}
        if len(dimensions) != 1:
            raise RuntimeError("Embedding shard dimensions do not match")
        return np.concatenate(matrices, axis=0)

    def _registered_shard(
        self, task_id: str, cache_key: str
    ) -> EmbeddingShard | None:
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
            raise RuntimeError("Registered embedding artifact changed on disk")
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

    @staticmethod
    def _sample_digest(samples: tuple[EmbeddingSample, ...]) -> str:
        payload = [
            [sample.sample_id, sample.pixel_sha256, sample.artist_scope]
            for sample in samples
        ]
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
            phase=TaskStatus.SEMANTIC_CLUSTERING,
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
        if self._control_requested(token.task_id):
            return self._commit_control(
                token, config_hash, batch_index, completed, completed, cursor
            )
        try:
            return self.tasks.complete_phase(
                token, phase=TaskStatus.SEMANTIC_CLUSTERING
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
        next_shard: int,
        next_scope: int,
        artifact_keys: list[str],
        sae_cache_key: str | None,
        embedding_identity: str,
        sample_digest: str,
        hierarchy_hash: str,
        inferred: int,
        cached: int,
        clusters_prepared: bool,
    ) -> dict:
        return {
            "next_shard": next_shard,
            "next_scope": next_scope,
            "artifact_keys": list(artifact_keys),
            "sae_cache_key": sae_cache_key,
            "embedding_identity": embedding_identity,
            "sample_digest": sample_digest,
            "hierarchy_hash": hierarchy_hash,
            "inferred_samples": inferred,
            "cached_samples": cached,
            "clusters_prepared": clusters_prepared,
        }

    @staticmethod
    def _summary(
        task_id: str,
        samples: tuple[EmbeddingSample, ...],
        shards: list[EmbeddingShard],
        inferred: int,
        cached: int,
        scopes: tuple[ClusteringScope, ...],
        nodes: int,
        sae: SAEArtifact | None,
        status: str,
    ) -> ClusteringSummary:
        return ClusteringSummary(
            task_id=task_id,
            eligible_samples=len(samples),
            embedding_shards=len(shards),
            inferred_samples=inferred,
            cached_samples=cached,
            cluster_scopes=len(scopes),
            cluster_nodes=nodes,
            sae_features=sae.feature_count if sae is not None else 0,
            final_status=status,
        )
