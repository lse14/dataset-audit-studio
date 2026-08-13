from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pytest
import torch
from dataset_audit_studio.clustering.config import (
    ClusteringConfig,
    SAEConfig,
    SelectionConfig,
)
from dataset_audit_studio.clustering.dedupe import (
    exact_duplicate_groups,
    excluded_non_representatives,
    semantic_duplicate_groups,
    visual_duplicate_groups,
)
from dataset_audit_studio.clustering.hierarchy import (
    hierarchical_clusters,
    leaf_coverage_order,
)
from dataset_audit_studio.clustering.quota import (
    allocate_sqrt_quota,
    select_diverse,
)
from dataset_audit_studio.clustering.repository import ClusteringRepository
from dataset_audit_studio.clustering.sae import train_sparse_autoencoder
from dataset_audit_studio.clustering.shards import EmbeddingShardStore
from dataset_audit_studio.clustering.torch_runtime import _image_feature_tensor
from pydantic import ValidationError
from transformers.modeling_outputs import BaseModelOutputWithPooling

TASK_ID = "00000000-0000-0000-0000-000000000001"


def test_siglip2_image_features_use_the_pooled_model_output() -> None:
    pooled = torch.tensor([[3.0, 4.0]], dtype=torch.float32)
    output = BaseModelOutputWithPooling(
        last_hidden_state=torch.zeros((1, 2, 2)),
        pooler_output=pooled,
    )
    assert _image_feature_tensor(output) is pooled
    assert _image_feature_tensor(pooled) is pooled
    with pytest.raises(RuntimeError, match="pooled tensor"):
        _image_feature_tensor(object())


def test_clustering_config_defaults_semantic_duplicate_threshold_to_0_92() -> None:
    assert ClusteringConfig().semantic_duplicate_threshold == pytest.approx(0.92)
    assert (
        ClusteringConfig.from_task_config({}).semantic_duplicate_threshold
        == pytest.approx(0.92)
    )


def test_persist_cluster_scope_defaults_semantic_duplicate_threshold_to_0_92() -> None:
    signature = inspect.signature(ClusteringRepository.persist_cluster_scope)
    assert (
        signature.parameters["semantic_duplicate_threshold"].default
        == pytest.approx(0.92)
    )


def test_clustering_config_is_strict_and_legacy_three_stage_config_is_closed() -> None:
    clustering = ClusteringConfig.from_task_config({})
    assert clustering.scope_mode == "artist"
    assert clustering.target_leaf_size == 128
    assert clustering.sae.enabled is False
    selection = SelectionConfig.from_task_config({})
    assert selection.model_dump() == {}
    with pytest.raises(ValidationError, match="minimum_split_size"):
        ClusteringConfig.from_task_config(
            {"clustering": {"minimum_split_size": 256, "target_leaf_size": 128}}
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        SelectionConfig.from_task_config(
            {
                "selection": {
                    "stages": [
                        {
                            "aesthetic_minimum": 1.5,
                            "maximum_ratio": 0.8,
                            "technical_strictness": "fatal",
                        }
                    ]
                }
            }
        )


def test_embedding_shard_is_atomic_self_describing_and_tamper_evident(
    tmp_path: Path,
) -> None:
    store = EmbeddingShardStore(project_root=tmp_path)
    sample_ids = ("sample-a", "sample-b")
    pixel_hashes = ("a" * 64, "b" * 64)
    embeddings = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    shard = store.write(
        task_id=TASK_ID,
        sample_ids=sample_ids,
        pixel_hashes=pixel_hashes,
        embeddings=embeddings,
        model_sha256="c" * 64,
        preprocessing_version="test-v1",
    )
    assert shard.sample_ids == sample_ids
    assert shard.pixel_hashes == pixel_hashes
    assert shard.rows == 2 and shard.dimensions == 2
    assert np.array_equal(store.load(shard), embeddings)
    assert not list(tmp_path.rglob("*.part"))

    path = tmp_path.joinpath(*Path(shard.relative_path).parts)
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError, match="SHA-256 changed"):
        store.load(shard)


def _cluster_fixture() -> tuple[np.ndarray, tuple[str, ...]]:
    rng = np.random.default_rng(7)
    centers = np.eye(4, dtype=np.float32)
    rows = []
    for center in centers:
        rows.append(center + rng.normal(0.0, 0.02, size=(65, 4)))
    matrix = np.concatenate(rows).astype(np.float32)
    keys = tuple(f"sample-{index:04d}" for index in range(len(matrix)))
    return matrix, keys


def test_faiss_hierarchy_is_deterministic_and_leaf_bounded() -> None:
    matrix, keys = _cluster_fixture()
    config = ClusteringConfig(
        minimum_split_size=32,
        target_leaf_size=64,
        max_branching=8,
        kmeans_iterations=10,
        seed=20260717,
    )
    first = hierarchical_clusters(
        matrix,
        keys,
        scope_kind="artist",
        scope_id="alaskanya",
        config=config,
    )
    second = hierarchical_clusters(
        matrix,
        keys,
        scope_kind="artist",
        scope_id="alaskanya",
        config=config,
    )
    def signature(nodes):
        return [
            (node.cluster_key, node.parent_key, node.sample_indices, node.is_leaf)
            for node in nodes
        ]
    assert signature(first) == signature(second)
    leaves = [node for node in first if node.is_leaf]
    assert len(leaves) >= 4
    assert max(len(node.sample_indices) for node in leaves) <= 64
    assert set(leaf_coverage_order(first)) == {
        node.cluster_key for node in leaves
    }


def test_artist_cluster_keys_keep_scopes_separate() -> None:
    matrix = np.eye(3, dtype=np.float32)
    keys = ("a", "b", "c")
    config = ClusteringConfig(target_leaf_size=64)
    left = hierarchical_clusters(
        matrix,
        keys,
        scope_kind="artist",
        scope_id="artist-a",
        config=config,
    )
    right = hierarchical_clusters(
        matrix,
        keys,
        scope_kind="artist",
        scope_id="artist-b",
        config=config,
    )
    assert len(left) == len(right) == 1
    assert left[0].cluster_key != right[0].cluster_key
    assert left[0].scope_id == "artist-a"
    assert right[0].scope_id == "artist-b"


def test_exact_visual_and_semantic_duplicate_layers_choose_ranked_representatives() -> None:
    def rank(index: int):
        return index != 1, index

    exact = exact_duplicate_groups(
        (0, 1, 2),
        ("same", "same", "other"),
        rank,
    )
    assert exact[0].member_indices == (0, 1)
    assert exact[0].representative_index == 1
    assert excluded_non_representatives(exact) == {0}

    visual = visual_duplicate_groups(
        (0, 1, 2),
        ("0000", "0001", "ffff"),
        ("00", "01", "ff"),
        phash_max_distance=1,
        colorhash_max_distance=1,
        rank=rank,
    )
    assert [(group.member_indices, group.representative_index) for group in visual] == [
        ((0, 1), 1)
    ]

    embeddings = np.array(
        [[1.0, 0.0], [0.9999, 0.01], [0.0, 1.0]],
        dtype=np.float32,
    )
    semantic = semantic_duplicate_groups(
        (0, 1, 2),
        embeddings,
        threshold=0.99,
        rank=rank,
    )
    assert [(group.member_indices, group.representative_index) for group in semantic] == [
        ((0, 1), 1)
    ]


def test_semantic_duplicate_groups_use_stable_ids_and_record_direct_scores() -> None:
    embeddings = np.asarray(
        [
            [1.0, 0.0],
            [0.9999, 0.01],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    )

    first = semantic_duplicate_groups(
        (0, 1, 2),
        embeddings,
        threshold=0.99,
        rank=lambda index: (index,),
        stable_keys=("sample-a", "sample-b", "sample-c"),
    )
    repeated = semantic_duplicate_groups(
        (0, 1, 2),
        embeddings,
        threshold=0.99,
        rank=lambda index: (index,),
        stable_keys=("sample-a", "sample-b", "sample-c"),
    )
    other_scope = semantic_duplicate_groups(
        (0, 1, 2),
        embeddings,
        threshold=0.99,
        rank=lambda index: (index,),
        stable_keys=("sample-x", "sample-y", "sample-z"),
    )

    assert len(first) == 1
    assert first[0].group_key == repeated[0].group_key
    assert first[0].group_key != other_scope[0].group_key
    assert first[0].member_indices == (0, 1)
    assert first[0].member_scores == pytest.approx((0.9999, 0.9999), abs=1e-3)

    with pytest.raises(ValueError, match="stable keys"):
        semantic_duplicate_groups(
            (0, 1),
            embeddings,
            threshold=0.99,
            rank=lambda index: (index,),
            stable_keys=("only-one",),
        )


def test_sqrt_quota_covers_leaves_before_weighting_and_handles_short_budget() -> None:
    sizes = {"a": 100, "b": 25, "c": 4}
    allocation = allocate_sqrt_quota(sizes, 9, ("a", "b", "c"))
    assert sum(allocation.values()) == 9
    assert all(allocation[key] >= 1 for key in sizes)
    assert allocation["a"] >= allocation["b"] >= allocation["c"]
    short = allocate_sqrt_quota(sizes, 2, ("b", "c", "a"))
    assert short == {"a": 0, "b": 1, "c": 1}


def test_diverse_selection_avoids_near_copy_after_best_ranked_anchor() -> None:
    embeddings = np.array(
        [[1.0, 0.0], [0.999, 0.001], [0.0, 1.0]],
        dtype=np.float32,
    )
    selected = select_diverse(
        (0, 1, 2),
        embeddings,
        2,
        {0: (0, 0), 1: (0, 1), 2: (0, 2)},
    )
    assert selected == (0, 2)


def test_sparse_autoencoder_is_seeded_and_emits_feature_review_inputs() -> None:
    rng = np.random.default_rng(11)
    embeddings = rng.normal(size=(24, 6)).astype(np.float32)
    config = SAEConfig(
        enabled=True,
        feature_count=8,
        epochs=2,
        batch_size=8,
        top_k=3,
        seed=23,
    )
    first = train_sparse_autoencoder(embeddings, config)
    second = train_sparse_autoencoder(embeddings, config)
    assert first.activations.shape == (24, 8)
    assert first.thresholds.shape == (8,)
    assert all(len(indices) == 3 for indices in first.top_indices)
    assert np.array_equal(first.activations, second.activations)
    assert first.top_indices == second.top_indices
    assert first.losses == second.losses
    assert set(first.state_dict) == set(second.state_dict)
    assert all(
        torch.equal(first.state_dict[key], second.state_dict[key])
        for key in first.state_dict
    )
