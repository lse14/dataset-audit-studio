from __future__ import annotations

import math

import numpy as np


def allocate_sqrt_quota(
    leaf_sizes: dict[str, int], target: int, coverage_order: tuple[str, ...]
) -> dict[str, int]:
    keys = [key for key in coverage_order if leaf_sizes.get(key, 0) > 0]
    allocation = {key: 0 for key in leaf_sizes}
    target = min(max(target, 0), sum(leaf_sizes.get(key, 0) for key in keys))
    if target == 0:
        return allocation
    if target < len(keys):
        for key in keys[:target]:
            allocation[key] = 1
        return allocation
    for key in keys:
        allocation[key] = 1
    remaining = target - len(keys)
    while remaining:
        available = {
            key: leaf_sizes[key] - allocation[key]
            for key in keys
            if allocation[key] < leaf_sizes[key]
        }
        if not available:
            break
        weights = {key: math.sqrt(leaf_sizes[key]) for key in available}
        weight_sum = sum(weights.values())
        ideals = {key: remaining * weights[key] / weight_sum for key in available}
        added = 0
        for key in available:
            amount = min(available[key], int(math.floor(ideals[key])))
            allocation[key] += amount
            remaining -= amount
            added += amount
        if remaining == 0:
            break
        order = sorted(available, key=lambda key: (-(ideals[key] - math.floor(ideals[key])), key))
        for key in order:
            if remaining == 0:
                break
            if allocation[key] < leaf_sizes[key]:
                allocation[key] += 1
                remaining -= 1
                added += 1
        if added == 0:
            break
    return allocation


def select_diverse(
    indices: tuple[int, ...], embeddings: np.ndarray, quota: int, rank_keys: dict[int, tuple]
) -> tuple[int, ...]:
    if quota <= 0 or not indices:
        return ()
    quota = min(quota, len(indices))
    matrix = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    normalized = matrix / np.maximum(norms, 1e-12)
    remaining = set(indices)
    selected = [min(remaining, key=lambda index: rank_keys[index])]
    remaining.remove(selected[0])
    while remaining and len(selected) < quota:
        selected_matrix = normalized[selected]

        def choice_key(index: int, selected_values=selected_matrix):
            nearest_similarity = float((selected_values @ normalized[index]).max())
            rank = rank_keys[index]
            return (rank[0], nearest_similarity, *rank[1:])

        chosen = min(remaining, key=choice_key)
        selected.append(chosen)
        remaining.remove(chosen)
    return tuple(selected)


__all__ = ["allocate_sqrt_quota", "select_diverse"]
