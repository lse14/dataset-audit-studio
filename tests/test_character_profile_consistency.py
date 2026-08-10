from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.clustering.repository import ClusteringRepository
from dataset_audit_studio.clustering.types import (
    ClusteringScope,
    EmbeddingSample,
    EmbeddingShard,
)
from dataset_audit_studio.components.cluster_hierarchy import algorithm
from dataset_audit_studio.components.cluster_hierarchy.config import HierarchyConfig
from dataset_audit_studio.database.models import Evidence, Sample
from sqlalchemy import select


def test_character_consistency_uses_calibrated_defaults() -> None:
    assert algorithm.character_consistency_config_payload() == {
        "minimum_scope_size": 4,
        "outlier_sigma": 2.04,
        "max_iterations": 2,
    }


def test_character_profile_enables_semantic_folder_consistency_components() -> None:
    profile = materialize_profile("character_concept")

    assert profile["components"]["embedding.semantic"]["enabled"] is True
    assert profile["components"]["cluster.hierarchy"]["enabled"] is True
    assert profile["components"]["cluster.hierarchy"]["config"]["scope_mode"] == "concept"

    materialized = ComponentTaskConfigMaterializer().materialize(
        profile["components"],
        profile="character_concept",
        require_profile=True,
    )
    assert materialized["clustering"]["enabled"] is True
    assert materialized["clustering"]["scope_mode"] == "concept"


def test_concept_scopes_keep_each_first_level_folder_separate() -> None:
    def sample(sample_id: str, relative_path: str, scope: str) -> EmbeddingSample:
        return EmbeddingSample(
            sample_id=sample_id,
            relative_path=relative_path,
            artist_scope=scope,
            source_path=Path(relative_path),
            image_path=Path(relative_path),
            source_size=1,
            source_mtime_ns=1,
            source_sha256="a" * 64,
            pixel_sha256="b" * 64,
        )

    samples = (
        sample("a-1", "alpha/one.png", "alpha"),
        sample("a-2", "alpha/two.png", "alpha"),
        sample("b-1", "beta/one.png", "beta"),
    )

    scopes = ClusteringRepository.scopes(samples, artist_mode=True)

    assert [(scope.scope_id, scope.sample_indices) for scope in scopes] == [
        ("alpha", (0, 1)),
        ("beta", (2,)),
    ]


def test_character_scope_analysis_marks_only_the_semantic_outlier() -> None:
    analyze = getattr(algorithm, "analyze_character_scope", None)
    assert callable(analyze), "character scope consistency analysis is missing"
    embeddings = np.asarray(
        [
            [1.0, 0.01],
            [1.0, 0.00],
            [1.0, -0.01],
            [1.0, 0.02],
            [1.0, -0.02],
            [0.0, 1.00],
        ],
        dtype=np.float32,
    )

    assessments = analyze(
        ("core-a", "core-b", "core-c", "core-d", "core-e", "other"),
        embeddings,
    )

    assert [item.sample_id for item in assessments if item.review_required] == ["other"]
    outlier = assessments[-1]
    assert outlier.centroid_similarity < outlier.threshold
    assert outlier.core_member is False
    assert outlier.reason == "semantic_similarity_below_scope_threshold"


def test_character_scope_analysis_does_not_guess_for_tiny_folders() -> None:
    analyze = getattr(algorithm, "analyze_character_scope", None)
    assert callable(analyze), "character scope consistency analysis is missing"

    assessments = analyze(
        ("left", "right", "third"),
        np.eye(3, dtype=np.float32),
    )

    assert all(item.review_required is False for item in assessments)
    assert all(item.reason == "insufficient_scope_size" for item in assessments)


def test_character_hierarchy_persists_reviewable_role_outlier_evidence(
    database,
    task_service,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    task = task_service.create_task(
        name="character consistency",
        source_root=str(source),
        output_root=None,
        config=materialize_profile("character_concept"),
    )
    sample_ids = ("core-a", "core-b", "core-c", "core-d", "core-e", "other")
    embeddings = np.asarray(
        [
            [1.0, 0.01],
            [1.0, 0.0],
            [1.0, -0.01],
            [1.0, 0.02],
            [1.0, -0.02],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    )
    samples = tuple(
        EmbeddingSample(
            sample_id=sample_id,
            relative_path=f"alpha/{sample_id}.png",
            artist_scope="alpha",
            source_path=source / "alpha" / f"{sample_id}.png",
            image_path=source / "alpha" / f"{sample_id}.png",
            source_size=1,
            source_mtime_ns=1,
            source_sha256=f"{index + 1:x}" * 64,
            pixel_sha256=f"{index + 5:x}" * 64,
        )
        for index, sample_id in enumerate(sample_ids)
    )
    with database.write_session() as session:
        session.add_all(
            Sample(
                id=item.sample_id,
                task_id=task.id,
                relative_path=item.relative_path,
                source_size=1,
                source_mtime_ns=1,
                source_sha256=item.source_sha256,
                pixel_sha256=item.pixel_sha256,
                media_kind="image",
                artist_scope=item.artist_scope,
                scan_state="valid",
            )
            for item in samples
        )
    nodes = algorithm.hierarchical_clusters(
        embeddings,
        tuple(item.relative_path for item in samples),
        scope_kind="concept",
        scope_id="alpha",
        config=HierarchyConfig(),
    )
    provenance = ClusteringRepository.character_consistency_metadata(
        [
            EmbeddingShard(
                cache_key="cache-key",
                relative_path="artifacts/embedding.safetensors",
                sample_ids=sample_ids,
                pixel_hashes=tuple(item.pixel_sha256 for item in samples),
                model_sha256="a" * 64,
                preprocessing_version="siglip2-naflex-image-processor-v1",
                sha256="b" * 64,
                size_bytes=1,
                rows=6,
                dimensions=2,
            )
        ]
    )

    with database.write_session() as session:
        ClusteringRepository().persist_cluster_scope(
            session,
            task_id=task.id,
            scope=ClusteringScope("alpha", (0, 1, 2, 3, 4, 5)),
            nodes=nodes,
            samples=samples,
            scope_embeddings=embeddings,
            hierarchy_config_hash="f" * 64,
            prepare=True,
            character_consistency=provenance,
        )
    with database.read_session() as session:
        evidence = session.scalars(
            select(Evidence).where(Evidence.task_id == task.id)
        ).all()

    assert [(item.sample_id, item.code) for item in evidence] == [
        ("other", "character_role_outlier")
    ]
    assert evidence[0].source == "siglip2_character_consistency_v1"
    assert evidence[0].severity == "medium"
    assert evidence[0].review_only is True
    assert evidence[0].value_number < evidence[0].threshold_number
    assert evidence[0].metadata_json["scope_id"] == "alpha"
    assert evidence[0].metadata_json["scope_size"] == 6
    assert evidence[0].metadata_json["core_size"] == 5
    assert evidence[0].metadata_json["model_sha256"] == "a" * 64
    assert evidence[0].metadata_json["algorithm_config_hash"]


def test_character_evidence_cleanup_chunks_sqlite_parameters(database) -> None:
    sample_ids = tuple(f"sample-{index}" for index in range(1_201))
    with database.write_session() as session:
        driver_connection = session.connection().connection.driver_connection
        previous_limit = driver_connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        try:
            ClusteringRepository._delete_character_consistency_evidence(
                session,
                task_id="task-id",
                sample_ids=sample_ids,
            )
        finally:
            driver_connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)
