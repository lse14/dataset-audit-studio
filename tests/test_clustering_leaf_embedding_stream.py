from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.clustering import repository as clustering_repository
from dataset_audit_studio.clustering.dedupe import semantic_duplicate_groups
from dataset_audit_studio.clustering.repository import ClusteringRepository
from dataset_audit_studio.clustering.shards import EmbeddingShardStore
from dataset_audit_studio.clustering.types import (
    ClusteringScope,
    ClusterPlanNode,
    EmbeddingSample,
    EmbeddingShard,
)
from dataset_audit_studio.database.models import Evidence, Sample
from sqlalchemy import select

TASK_ID = "00000000-0000-0000-0000-000000000009"


def _sample(
    sample_id: str,
    relative_path: str,
    *,
    source: Path,
) -> EmbeddingSample:
    return EmbeddingSample(
        sample_id=sample_id,
        relative_path=relative_path,
        artist_scope="artist",
        source_path=source / relative_path,
        image_path=source / relative_path,
        source_size=1,
        source_mtime_ns=1,
        source_sha256=f"{sample_id}-source".ljust(64, "0"),
        pixel_sha256=f"{sample_id}-pixel".ljust(64, "0"),
    )


def _leaf_nodes() -> tuple[ClusterPlanNode, ...]:
    root_key = "test:root"
    return (
        ClusterPlanNode(
            cluster_key=root_key,
            parent_key=None,
            scope_kind="artist",
            scope_id="artist",
            level=0,
            sample_indices=(0, 1, 2, 3, 4),
            centroid=np.asarray([0.5, 0.5], dtype=np.float32),
            representative_index=0,
            is_leaf=False,
        ),
        ClusterPlanNode(
            cluster_key="test:leaf-a",
            parent_key=root_key,
            scope_kind="artist",
            scope_id="artist",
            level=1,
            sample_indices=(0, 1),
            centroid=np.asarray([1.0, 0.0], dtype=np.float32),
            representative_index=0,
            is_leaf=True,
        ),
        ClusterPlanNode(
            cluster_key="test:leaf-b",
            parent_key=root_key,
            scope_kind="artist",
            scope_id="artist",
            level=1,
            sample_indices=(2, 3),
            centroid=np.asarray([0.0, 1.0], dtype=np.float32),
            representative_index=0,
            is_leaf=True,
        ),
        ClusterPlanNode(
            cluster_key="test:leaf-c",
            parent_key=root_key,
            scope_kind="artist",
            scope_id="artist",
            level=1,
            sample_indices=(4,),
            centroid=np.asarray([0.7, 0.7], dtype=np.float32),
            representative_index=0,
            is_leaf=True,
        ),
    )


def test_semantic_duplicate_leaf_path_fetches_only_member_embeddings(
    database,
    task_service,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    task = task_service.create_task(
        name="leaf embedding stream",
        source_root=str(source),
        output_root=None,
        config=materialize_profile("general"),
    )
    sample_ids = ("dup-a1", "dup-a2", "dup-b1", "dup-b2", "lonely")
    samples = tuple(
        _sample(sample_id, f"artist/{sample_id}.png", source=source)
        for sample_id in sample_ids
    )
    embeddings = np.asarray(
        [
            [1.0, 0.0],
            [0.9999, 0.01],
            [0.0, 1.0],
            [0.01, 0.9999],
            [0.7, 0.7],
        ],
        dtype=np.float32,
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

    captured: list[dict[str, object]] = []
    original = semantic_duplicate_groups

    def spy(indices, matrix, **kwargs):
        captured.append(
            {
                "indices": tuple(indices),
                "rows": int(matrix.shape[0]),
                "stable_keys": kwargs.get("stable_keys"),
            }
        )
        return original(indices, matrix, **kwargs)

    monkeypatch.setattr(clustering_repository, "semantic_duplicate_groups", spy)

    identity = {
        "model_id": "siglip2_so400m_naflex",
        "model_sha256": "a" * 64,
        "preprocessing_version": "siglip2-naflex-image-processor-v1",
        "embedding_identity_hash": "b" * 64,
    }
    executed_sql: list[str] = []

    with database.write_session() as session:
        original_execute = session.execute

        def spy_execute(statement, *args, **kwargs):
            executed_sql.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
            return original_execute(statement, *args, **kwargs)

        session.execute = spy_execute  # type: ignore[method-assign]
        ClusteringRepository().persist_cluster_scope(
            session,
            task_id=task.id,
            scope=ClusteringScope("artist", (0, 1, 2, 3, 4)),
            nodes=_leaf_nodes(),
            samples=samples,
            scope_embeddings=embeddings,
            hierarchy_config_hash="c" * 64,
            prepare=True,
            embedding_identity=identity,
        )

    assert captured == [
        {
            "indices": (0, 1),
            "rows": 2,
            "stable_keys": ("dup-a1", "dup-a2"),
        },
        {
            "indices": (0, 1),
            "rows": 2,
            "stable_keys": ("dup-b1", "dup-b2"),
        },
    ]
    assert all(item["rows"] != len(sample_ids) for item in captured)

    select_sql = [
        sql.casefold()
        for sql in executed_sql
        if sql.lstrip().upper().startswith("SELECT")
    ]
    assert all("select * " not in sql and "select *" not in sql for sql in select_sql)
    membership_sql = [sql for sql in select_sql if "cluster_memberships" in sql]
    assert membership_sql
    assert all("cluster_id" in sql for sql in membership_sql)
    sample_sql = [sql for sql in select_sql if "from samples" in sql]
    assert sample_sql
    assert all("source_sha256" not in sql and "phash" not in sql for sql in sample_sql)

    with database.read_session() as session:
        evidence = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task.id,
                Evidence.code == "duplicate_semantic",
            )
            .order_by(Evidence.sample_id)
        ).all()

    assert {row.sample_id for row in evidence} == {"dup-a1", "dup-a2", "dup-b1", "dup-b2"}
    assert "lonely" not in {row.sample_id for row in evidence}
    assert all(row.review_only is True for row in evidence)
    assert all(row.threshold_number == pytest.approx(0.92) for row in evidence)


def test_load_embeddings_for_sample_ids_only_loads_overlapping_shards(
    tmp_path: Path,
) -> None:
    store = EmbeddingShardStore(project_root=tmp_path)
    first = store.write(
        task_id=TASK_ID,
        sample_ids=("alpha", "bravo"),
        pixel_hashes=("1" * 64, "2" * 64),
        embeddings=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        model_sha256="c" * 64,
        preprocessing_version="test-v1",
    )
    second = store.write(
        task_id=TASK_ID,
        sample_ids=("charlie", "delta"),
        pixel_hashes=("3" * 64, "4" * 64),
        embeddings=np.asarray([[0.0, -1.0], [-1.0, 0.0]], dtype=np.float32),
        model_sha256="c" * 64,
        preprocessing_version="test-v1",
    )
    loaded_keys: list[str] = []

    def load_shard(shard: EmbeddingShard) -> np.ndarray:
        loaded_keys.append(shard.cache_key)
        return store.load(shard)

    matrix = ClusteringRepository.load_embeddings_for_sample_ids(
        ("delta", "charlie"),
        (first, second),
        load_shard,
    )

    assert loaded_keys == [second.cache_key]
    assert matrix.shape == (2, 2)
    np.testing.assert_allclose(matrix[0], [-1.0, 0.0])
    np.testing.assert_allclose(matrix[1], [0.0, -1.0])
