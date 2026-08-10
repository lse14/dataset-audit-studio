from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

import faiss
import numpy as np

from dataset_audit_studio.components.cluster_hierarchy.config import HierarchyConfig
from dataset_audit_studio.components.cluster_hierarchy.contracts import ClusterPlanNode


class ClusteringInterrupted(RuntimeError):
    pass


@dataclass(frozen=True)
class CharacterScopeAssessment:
    sample_id: str
    average_similarity: float
    centroid_similarity: float
    threshold: float | None
    core_member: bool
    review_required: bool
    reason: str


CHARACTER_CONSISTENCY_ALGORITHM_VERSION = "siglip2_character_consistency_v1"
_CHARACTER_MINIMUM_SCOPE_SIZE = 4
_CHARACTER_OUTLIER_SIGMA = 2.04
_CHARACTER_MAX_ITERATIONS = 2


def character_consistency_config_payload() -> dict[str, int | float]:
    return {
        "minimum_scope_size": _CHARACTER_MINIMUM_SCOPE_SIZE,
        "outlier_sigma": _CHARACTER_OUTLIER_SIGMA,
        "max_iterations": _CHARACTER_MAX_ITERATIONS,
    }


def character_consistency_config_hash() -> str:
    return hashlib.sha256(
        json.dumps(
            character_consistency_config_payload(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _normalized(values: np.ndarray) -> np.ndarray:
    matrix = np.ascontiguousarray(values, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise ValueError("Clustering requires a non-empty two-dimensional matrix")
    if np.any(~np.isfinite(matrix)):
        raise ValueError("Clustering embeddings contain non-finite values")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("Clustering embeddings contain zero-length rows")
    return matrix / norms


def _average_scope_similarity(matrix: np.ndarray, active: np.ndarray) -> np.ndarray:
    reference = matrix[active]
    count = len(reference)
    summed = reference.sum(axis=0, dtype=np.float64)
    similarities = matrix.astype(np.float64, copy=False) @ summed
    similarities /= count
    if count > 1:
        active_indices = np.flatnonzero(active)
        self_similarity = np.einsum(
            "ij,ij->i",
            matrix[active],
            matrix[active],
            dtype=np.float64,
        )
        similarities[active_indices] = (
            similarities[active_indices] * count - self_similarity
        ) / (count - 1)
    return similarities.astype(np.float32)


def analyze_character_scope(
    sample_ids: tuple[str, ...],
    embeddings: np.ndarray,
) -> tuple[CharacterScopeAssessment, ...]:
    matrix = _normalized(embeddings)
    if matrix.shape[0] != len(sample_ids):
        raise ValueError("Character scope sample ids do not match embedding rows")
    count = len(sample_ids)
    active = np.ones(count, dtype=bool)
    average = _average_scope_similarity(matrix, active)

    if count < _CHARACTER_MINIMUM_SCOPE_SIZE:
        centroid = matrix.mean(axis=0, dtype=np.float64)
        norm = float(np.linalg.norm(centroid))
        centroid_similarities = (
            matrix @ (centroid / norm)
            if norm > 0
            else np.zeros(count, dtype=np.float32)
        )
        return tuple(
            CharacterScopeAssessment(
                sample_id=sample_id,
                average_similarity=float(average[index]),
                centroid_similarity=float(centroid_similarities[index]),
                threshold=None,
                core_member=True,
                review_required=False,
                reason="insufficient_scope_size",
            )
            for index, sample_id in enumerate(sample_ids)
        )

    for _iteration in range(_CHARACTER_MAX_ITERATIONS):
        average = _average_scope_similarity(matrix, active)
        reference = average[active]
        threshold = float(
            reference.mean(dtype=np.float64)
            - _CHARACTER_OUTLIER_SIGMA * reference.std(dtype=np.float64)
        )
        candidates = active & (average < threshold)
        if not np.any(candidates) or int(active.sum() - candidates.sum()) < 2:
            break
        active[candidates] = False

    average = _average_scope_similarity(matrix, active)
    centroid = matrix[active].mean(axis=0, dtype=np.float64)
    norm = float(np.linalg.norm(centroid))
    if norm <= 0:
        centroid = matrix[int(np.flatnonzero(active)[np.argmax(average[active])])]
        norm = 1.0
    centroid_similarities = matrix @ (centroid / norm)
    reference = centroid_similarities[active]
    threshold = float(
        reference.mean(dtype=np.float64)
        - _CHARACTER_OUTLIER_SIGMA * reference.std(dtype=np.float64)
    )
    review = (~active) | (centroid_similarities < threshold - 1e-6)
    return tuple(
        CharacterScopeAssessment(
            sample_id=sample_id,
            average_similarity=float(average[index]),
            centroid_similarity=float(centroid_similarities[index]),
            threshold=threshold,
            core_member=not bool(review[index]),
            review_required=bool(review[index]),
            reason=(
                "semantic_similarity_below_scope_threshold"
                if review[index]
                else "semantic_core_member"
            ),
        )
        for index, sample_id in enumerate(sample_ids)
    )


def _centroid_and_representative(
    embeddings: np.ndarray, indices: np.ndarray
) -> tuple[np.ndarray, int]:
    centroid = embeddings[indices].mean(axis=0, dtype=np.float64).astype(np.float32)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid /= norm
    similarities = embeddings[indices] @ centroid
    best_position = int(np.argmax(similarities))
    return centroid, int(indices[best_position])


def _scope_prefix(scope_kind: str, scope_id: str) -> str:
    digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:16]
    return f"{scope_kind}:{digest}"


def hierarchical_clusters(
    embeddings: np.ndarray,
    sample_keys: tuple[str, ...],
    *,
    scope_kind: str,
    scope_id: str,
    config: HierarchyConfig,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[ClusterPlanNode, ...]:
    matrix = _normalized(embeddings)
    if matrix.shape[0] != len(sample_keys):
        raise ValueError("Clustering sample keys do not match embedding rows")
    prefix = _scope_prefix(scope_kind, scope_id)
    nodes: list[ClusterPlanNode] = []
    previous_threads = faiss.omp_get_max_threads()
    faiss.omp_set_num_threads(1)

    def visit(
        indices: np.ndarray,
        *,
        path: tuple[int, ...],
        parent_key: str | None,
        level: int,
    ) -> None:
        if should_stop is not None and should_stop():
            raise ClusteringInterrupted("Clustering control requested")
        suffix = "root" if not path else ".".join(str(value) for value in path)
        key = f"{prefix}:{suffix}"
        centroid, representative = _centroid_and_representative(matrix, indices)
        should_split = (
            len(indices) >= config.minimum_split_size
            and len(indices) > config.target_leaf_size
        )
        groups: list[np.ndarray] = []
        if should_split:
            branches = min(
                config.max_branching,
                max(2, math.ceil(len(indices) / config.target_leaf_size)),
            )
            branches = min(branches, len(indices))
            seed_payload = f"{config.seed}:{scope_id}:{suffix}".encode()
            seed = int.from_bytes(hashlib.sha256(seed_payload).digest()[:4], "big") & (
                2**31 - 1
            )
            kmeans = faiss.Kmeans(
                matrix.shape[1],
                branches,
                niter=config.kmeans_iterations,
                nredo=1,
                seed=seed,
                spherical=True,
                verbose=False,
            )
            local = np.ascontiguousarray(matrix[indices], dtype=np.float32)
            kmeans.train(local)
            labels = kmeans.index.search(local, 1)[1].reshape(-1)
            if should_stop is not None and should_stop():
                raise ClusteringInterrupted("Clustering control requested")
            for label in range(branches):
                members = indices[labels == label]
                if len(members):
                    groups.append(members)
            groups.sort(key=lambda group: min(sample_keys[index] for index in group))
            if len(groups) < 2:
                groups = []
        is_leaf = not groups
        nodes.append(
            ClusterPlanNode(
                cluster_key=key,
                parent_key=parent_key,
                scope_kind=scope_kind,
                scope_id=scope_id,
                level=level,
                sample_indices=tuple(int(index) for index in indices),
                centroid=centroid,
                representative_index=representative,
                is_leaf=is_leaf,
            )
        )
        for child_index, group in enumerate(groups):
            visit(
                group,
                path=(*path, child_index),
                parent_key=key,
                level=level + 1,
            )

    try:
        visit(
            np.arange(matrix.shape[0], dtype=np.int64),
            path=(),
            parent_key=None,
            level=0,
        )
    finally:
        faiss.omp_set_num_threads(previous_threads)
    return tuple(nodes)


def leaf_coverage_order(nodes: tuple[ClusterPlanNode, ...]) -> tuple[str, ...]:
    if not nodes:
        return ()
    by_key = {node.cluster_key: node for node in nodes}
    children: dict[str | None, list[str]] = defaultdict(list)
    for node in nodes:
        children[node.parent_key].append(node.cluster_key)
    for values in children.values():
        values.sort()

    def leaves(key: str) -> list[str]:
        node = by_key[key]
        if node.is_leaf:
            return [key]
        queues = deque(deque(leaves(child)) for child in children[key])
        ordered: list[str] = []
        while queues:
            queue = queues.popleft()
            ordered.append(queue.popleft())
            if queue:
                queues.append(queue)
        return ordered

    roots = children[None]
    if len(roots) != 1:
        raise ValueError("Cluster plan must contain exactly one root")
    return tuple(leaves(roots[0]))
