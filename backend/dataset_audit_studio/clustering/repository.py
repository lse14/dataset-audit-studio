from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from dataset_audit_studio.clustering.assets import SIGLIP_MODEL_ID
from dataset_audit_studio.clustering.types import (
    ClusteringScope,
    ClusterPlanNode,
    EmbeddingSample,
    EmbeddingShard,
    SAEArtifact,
)
from dataset_audit_studio.components.cluster_hierarchy.algorithm import (
    CHARACTER_CONSISTENCY_ALGORITHM_VERSION,
    analyze_character_scope,
    character_consistency_config_hash,
    character_consistency_config_payload,
)
from dataset_audit_studio.database.enums import ArtifactState
from dataset_audit_studio.database.models import (
    Artifact,
    ClusterMembership,
    ClusterNode,
    Evidence,
    ReviewDecision,
    Sample,
)
from dataset_audit_studio.jobs.types import TaskView
from dataset_audit_studio.scoring.repository import ScoringRepository

EMBEDDING_ARTIFACT_KIND = "siglip2_embeddings"
SAE_ARTIFACT_KIND = "siglip_sae"
CHARACTER_ROLE_EVIDENCE_CODE = "character_role_outlier"
CHARACTER_ROLE_EVIDENCE_SOURCE = CHARACTER_CONSISTENCY_ALGORITHM_VERSION
_CHARACTER_EVIDENCE_DELETE_BATCH_SIZE = 500


class ClusteringRepository:
    def __init__(self, *, project_root: Path | None = None) -> None:
        self.project_root = project_root

    def list_samples(
        self,
        session: Session,
        task: TaskView,
        *,
        artist_core_only: bool,
    ) -> tuple[EmbeddingSample, ...]:
        scoring_repository = (
            ScoringRepository(project_root=self.project_root)
            if self.project_root is not None
            else ScoringRepository()
        )
        inputs = scoring_repository.list_inputs(session, task)
        # Inputs already come from this task. Avoid a giant ``IN`` list here:
        # SQLite rejects queries that exceed its bind-variable limit.
        sample_rows = session.scalars(
            select(Sample).where(Sample.task_id == task.id)
        ).all()
        by_id = {row.id: row for row in sample_rows}
        excluded_ai = set(
            session.scalars(
                select(ReviewDecision.sample_id).where(
                    ReviewDecision.task_id == task.id,
                    ReviewDecision.category == "ai_generated",
                    ReviewDecision.decision == "approved_exclude",
                    ReviewDecision.is_active.is_(True),
                    ReviewDecision.sample_id.is_not(None),
                )
            ).all()
        )
        outside_domain = set(
            session.scalars(
                select(Evidence.sample_id).where(
                    Evidence.task_id == task.id,
                    Evidence.code == "in_domain_probability",
                    Evidence.value_number.is_not(None),
                    Evidence.threshold_number.is_not(None),
                    Evidence.value_number < Evidence.threshold_number,
                )
            ).all()
        )
        style_core: dict[str, bool] = {}
        style_rows = session.scalars(
            select(Evidence).where(
                Evidence.task_id == task.id,
                Evidence.code == "artist_style_score",
                Evidence.source == "artist_style_v1",
            )
        ).all()
        for row in style_rows:
            style_core[row.sample_id] = bool(row.metadata_json.get("core_member", False))
        style_overrides = set(
            session.scalars(
                select(ReviewDecision.sample_id).where(
                    ReviewDecision.task_id == task.id,
                    ReviewDecision.category == "style_outlier",
                    ReviewDecision.decision == "approved_keep",
                    ReviewDecision.source == "human",
                    ReviewDecision.is_active.is_(True),
                    ReviewDecision.sample_id.is_not(None),
                )
            ).all()
        )
        samples: list[EmbeddingSample] = []
        for item in inputs:
            if item.sample_id in excluded_ai or item.sample_id in outside_domain:
                continue
            if artist_core_only:
                if item.sample_id not in style_core:
                    raise RuntimeError(
                        "Artist clustering requires completed style evidence for every sample"
                    )
                if not style_core[item.sample_id] and item.sample_id not in style_overrides:
                    continue
            row = by_id[item.sample_id]
            samples.append(
                EmbeddingSample(
                    sample_id=item.sample_id,
                    relative_path=item.relative_path,
                    artist_scope=item.artist_scope,
                    source_path=item.source_path,
                    image_path=item.image_path,
                    source_size=item.source_size,
                    source_mtime_ns=item.source_mtime_ns,
                    source_sha256=row.source_sha256,
                    pixel_sha256=item.pixel_sha256,
                )
            )
        return tuple(sorted(samples, key=lambda sample: sample.relative_path))

    @staticmethod
    def scopes(
        samples: tuple[EmbeddingSample, ...],
        *,
        artist_mode: bool,
    ) -> tuple[ClusteringScope, ...]:
        if not samples:
            return ()
        if not artist_mode:
            return (ClusteringScope("__all__", tuple(range(len(samples)))),)
        grouped: dict[str, list[int]] = {}
        for index, sample in enumerate(samples):
            grouped.setdefault(sample.artist_scope, []).append(index)
        return tuple(
            ClusteringScope(scope_id, tuple(indices))
            for scope_id, indices in sorted(grouped.items())
        )

    @staticmethod
    def register_embedding_shard(
        session: Session,
        *,
        task_id: str,
        shard: EmbeddingShard,
    ) -> None:
        ClusteringRepository._upsert_artifact(
            session,
            task_id=task_id,
            kind=EMBEDDING_ARTIFACT_KIND,
            phase="semantic_clustering",
            cache_key=shard.cache_key,
            path=shard.relative_path,
            sha256=shard.sha256,
            size_bytes=shard.size_bytes,
            metadata={
                "sample_ids": list(shard.sample_ids),
                "pixel_hashes": list(shard.pixel_hashes),
                "model_sha256": shard.model_sha256,
                "preprocessing_version": shard.preprocessing_version,
                "rows": shard.rows,
                "dimensions": shard.dimensions,
            },
        )

    @staticmethod
    def register_sae(
        session: Session,
        *,
        task_id: str,
        artifact: SAEArtifact,
    ) -> None:
        ClusteringRepository._upsert_artifact(
            session,
            task_id=task_id,
            kind=SAE_ARTIFACT_KIND,
            phase="semantic_clustering",
            cache_key=artifact.cache_key,
            path=artifact.relative_path,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            metadata={
                "sample_ids": list(artifact.sample_ids),
                "input_dimensions": artifact.input_dimensions,
                "feature_count": artifact.feature_count,
                "thresholds": list(artifact.thresholds),
                "top_indices": [list(values) for values in artifact.top_indices],
                "losses": list(artifact.losses),
            },
        )

    @staticmethod
    def artifact_snapshot(
        session: Session,
        *,
        task_id: str,
        kind: str,
        cache_key: str,
    ) -> dict | None:
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.task_id == task_id,
                Artifact.kind == kind,
                Artifact.cache_key == cache_key,
            )
        )
        if artifact is None:
            return None
        return {
            "path": artifact.path,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "state": artifact.state,
        }

    @staticmethod
    def _upsert_artifact(
        session: Session,
        *,
        task_id: str,
        kind: str,
        phase: str,
        cache_key: str,
        path: str,
        sha256: str,
        size_bytes: int,
        metadata: dict,
    ) -> None:
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.task_id == task_id,
                Artifact.kind == kind,
                Artifact.cache_key == cache_key,
            )
        )
        if artifact is None:
            artifact = Artifact(
                task_id=task_id,
                sample_id=None,
                kind=kind,
                phase=phase,
                cache_key=cache_key,
                path=path,
                sha256=sha256,
                size_bytes=size_bytes,
                state=ArtifactState.READY.value,
                metadata_json=metadata,
            )
            session.add(artifact)
        else:
            artifact.path = path
            artifact.sha256 = sha256
            artifact.size_bytes = size_bytes
            artifact.state = ArtifactState.READY.value
            artifact.metadata_json = metadata

    @staticmethod
    def persist_cluster_scope(
        session: Session,
        *,
        task_id: str,
        scope: ClusteringScope,
        nodes: tuple[ClusterPlanNode, ...],
        samples: tuple[EmbeddingSample, ...],
        scope_embeddings: np.ndarray,
        hierarchy_config_hash: str,
        prepare: bool,
        character_consistency: Mapping[str, object] | None = None,
    ) -> None:
        if prepare:
            session.execute(delete(ClusterNode).where(ClusterNode.task_id == task_id))
            session.execute(
                delete(Evidence).where(
                    Evidence.task_id == task_id,
                    Evidence.source == CHARACTER_ROLE_EVIDENCE_SOURCE,
                )
            )
        id_by_key: dict[str, str] = {}
        local_sample_ids = [samples[index].sample_id for index in scope.sample_indices]
        for node in nodes:
            parent_id = id_by_key.get(node.parent_key) if node.parent_key else None
            representative_sample_id = local_sample_ids[node.representative_index]
            row = ClusterNode(
                task_id=task_id,
                parent_id=parent_id,
                cluster_key=node.cluster_key,
                scope_kind=node.scope_kind,
                scope_id=node.scope_id,
                level=node.level,
                label=None,
                size=len(node.sample_indices),
                centroid_artifact_id=None,
                metadata_json={
                    "is_leaf": node.is_leaf,
                    "representative_sample_id": representative_sample_id,
                    "hierarchy_config_hash": hierarchy_config_hash,
                },
            )
            session.add(row)
            session.flush()
            id_by_key[node.cluster_key] = row.id
            similarities = scope_embeddings[list(node.sample_indices)] @ node.centroid
            for position, local_index in enumerate(node.sample_indices):
                session.add(
                    ClusterMembership(
                        cluster_id=row.id,
                        sample_id=local_sample_ids[local_index],
                        task_id=task_id,
                        score=float(similarities[position]),
                        is_representative=local_index == node.representative_index,
                    )
                )
        if character_consistency is not None:
            ClusteringRepository._persist_character_consistency(
                session,
                task_id=task_id,
                scope=scope,
                sample_ids=tuple(local_sample_ids),
                embeddings=scope_embeddings,
                hierarchy_config_hash=hierarchy_config_hash,
                provenance=character_consistency,
            )

    @staticmethod
    def character_consistency_metadata(
        shards: tuple[EmbeddingShard, ...] | list[EmbeddingShard],
    ) -> dict[str, object]:
        if not shards:
            raise RuntimeError("Character consistency requires embedding shards")
        embedding_versions = {
            (shard.model_sha256, shard.preprocessing_version) for shard in shards
        }
        if len(embedding_versions) != 1:
            raise RuntimeError("Character consistency embedding shard identity is inconsistent")
        model_sha256, preprocessing_version = next(iter(embedding_versions))
        embedding_identity_hash = hashlib.sha256(
            json.dumps(
                {
                    "model_id": SIGLIP_MODEL_ID,
                    "model_sha256": model_sha256,
                    "preprocessing_version": preprocessing_version,
                    "shards": [shard.sha256 for shard in shards],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return {
            "model_id": SIGLIP_MODEL_ID,
            "model_sha256": model_sha256,
            "preprocessing_version": preprocessing_version,
            "embedding_identity_hash": embedding_identity_hash,
            "algorithm_version": CHARACTER_ROLE_EVIDENCE_SOURCE,
            "algorithm_config": character_consistency_config_payload(),
            "algorithm_config_hash": character_consistency_config_hash(),
        }

    @staticmethod
    def _persist_character_consistency(
        session: Session,
        *,
        task_id: str,
        scope: ClusteringScope,
        sample_ids: tuple[str, ...],
        embeddings: np.ndarray,
        hierarchy_config_hash: str,
        provenance: Mapping[str, object],
    ) -> None:
        ClusteringRepository._delete_character_consistency_evidence(
            session,
            task_id=task_id,
            sample_ids=sample_ids,
        )
        assessments = analyze_character_scope(sample_ids, embeddings)
        core_size = sum(item.core_member for item in assessments)
        for item in assessments:
            if not item.review_required:
                continue
            if item.threshold is None:
                raise RuntimeError("Character role candidate has no similarity threshold")
            session.add(
                Evidence(
                    task_id=task_id,
                    sample_id=item.sample_id,
                    code=CHARACTER_ROLE_EVIDENCE_CODE,
                    source=CHARACTER_ROLE_EVIDENCE_SOURCE,
                    value_json=item.centroid_similarity,
                    threshold_json=item.threshold,
                    value_number=item.centroid_similarity,
                    threshold_number=item.threshold,
                    metadata_json={
                        **provenance,
                        "scope_id": scope.scope_id,
                        "scope_size": len(sample_ids),
                        "core_size": core_size,
                        "average_similarity": item.average_similarity,
                        "centroid_similarity": item.centroid_similarity,
                        "reason": item.reason,
                        "hierarchy_config_hash": hierarchy_config_hash,
                    },
                    severity="medium",
                    review_only=True,
                    bbox_json=None,
                    algorithm_version=CHARACTER_ROLE_EVIDENCE_SOURCE,
                )
            )

    @staticmethod
    def _delete_character_consistency_evidence(
        session: Session,
        *,
        task_id: str,
        sample_ids: tuple[str, ...],
    ) -> None:
        unique_ids = tuple(dict.fromkeys(sample_ids))
        for offset in range(0, len(unique_ids), _CHARACTER_EVIDENCE_DELETE_BATCH_SIZE):
            batch = unique_ids[offset : offset + _CHARACTER_EVIDENCE_DELETE_BATCH_SIZE]
            session.execute(
                delete(Evidence).where(
                    Evidence.task_id == task_id,
                    Evidence.sample_id.in_(batch),
                    Evidence.source == CHARACTER_ROLE_EVIDENCE_SOURCE,
                )
            )

    @staticmethod
    def prepare_empty_clusters(session: Session, task_id: str) -> None:
        session.execute(delete(ClusterNode).where(ClusterNode.task_id == task_id))
        session.execute(
            delete(Evidence).where(
                Evidence.task_id == task_id,
                Evidence.source == CHARACTER_ROLE_EVIDENCE_SOURCE,
            )
        )

    @staticmethod
    def cluster_node_count(session: Session, task_id: str) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ClusterNode)
                .where(ClusterNode.task_id == task_id)
            )
            or 0
        )
