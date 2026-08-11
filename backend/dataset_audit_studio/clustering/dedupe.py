from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable

import faiss
import numpy as np

from dataset_audit_studio.clustering.types import DuplicateGroup

RankKey = tuple


class _UnionFind:
    def __init__(self, values: Iterable[int]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)

    def groups(self) -> tuple[tuple[int, ...], ...]:
        groups: dict[int, list[int]] = defaultdict(list)
        for value in self.parent:
            groups[self.find(value)].append(value)
        return tuple(
            tuple(sorted(values))
            for values in sorted(groups.values(), key=lambda group: min(group))
            if len(values) > 1
        )


class _BKNode:
    def __init__(self, value: int, index: int) -> None:
        self.value = value
        self.indices = [index]
        self.children: dict[int, _BKNode] = {}

    def add(self, value: int, index: int) -> None:
        distance = (self.value ^ value).bit_count()
        if distance == 0:
            self.indices.append(index)
            return
        child = self.children.get(distance)
        if child is None:
            self.children[distance] = _BKNode(value, index)
        else:
            child.add(value, index)

    def query(self, value: int, maximum_distance: int, output: list[int]) -> None:
        distance = (self.value ^ value).bit_count()
        if distance <= maximum_distance:
            output.extend(self.indices)
        lower = max(1, distance - maximum_distance)
        upper = distance + maximum_distance
        for edge, child in self.children.items():
            if lower <= edge <= upper:
                child.query(value, maximum_distance, output)


def _group_key(kind: str, members: tuple[int, ...]) -> str:
    payload = f"{kind}:" + ",".join(str(value) for value in members)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _stable_group_key(kind: str, members: tuple[str, ...]) -> str:
    payload = f"{kind}:" + "\0".join(sorted(members))
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _as_groups(
    kind: str,
    members: tuple[tuple[int, ...], ...],
    rank: Callable[[int], RankKey],
) -> tuple[DuplicateGroup, ...]:
    return tuple(
        DuplicateGroup(
            kind=kind,
            group_key=_group_key(kind, group),
            member_indices=group,
            representative_index=min(group, key=rank),
        )
        for group in members
    )


def exact_duplicate_groups(
    indices: tuple[int, ...],
    source_hashes: tuple[str, ...],
    rank: Callable[[int], RankKey],
) -> tuple[DuplicateGroup, ...]:
    by_hash: dict[str, list[int]] = defaultdict(list)
    for index in indices:
        by_hash[source_hashes[index]].append(index)
    members = tuple(tuple(sorted(group)) for _, group in sorted(by_hash.items()) if len(group) > 1)
    return _as_groups("exact", members, rank)


def visual_duplicate_groups(
    indices: tuple[int, ...],
    phashes: tuple[str | None, ...],
    colorhashes: tuple[str | None, ...],
    *,
    phash_max_distance: int,
    colorhash_max_distance: int,
    rank: Callable[[int], RankKey],
) -> tuple[DuplicateGroup, ...]:
    valid = [
        index
        for index in indices
        if phashes[index] is not None and colorhashes[index] is not None
    ]
    if len(valid) < 2:
        return ()
    tree = _BKNode(int(phashes[valid[0]] or "0", 16), valid[0])
    union = _UnionFind(valid)
    for index in valid[1:]:
        phash = int(phashes[index] or "0", 16)
        matches: list[int] = []
        tree.query(phash, phash_max_distance, matches)
        color = int(colorhashes[index] or "0", 16)
        for match in matches:
            other_color = int(colorhashes[match] or "0", 16)
            if (color ^ other_color).bit_count() <= colorhash_max_distance:
                union.union(index, match)
        tree.add(phash, index)
    return _as_groups("visual", union.groups(), rank)


def semantic_duplicate_groups(
    indices: tuple[int, ...],
    embeddings: np.ndarray,
    *,
    threshold: float,
    rank: Callable[[int], RankKey],
    stable_keys: tuple[str, ...] | None = None,
) -> tuple[DuplicateGroup, ...]:
    if len(indices) < 2:
        return ()
    if stable_keys is not None and len(stable_keys) != len(embeddings):
        raise ValueError("Semantic duplicate stable keys do not match embedding rows")
    matrix = np.ascontiguousarray(embeddings[list(indices)], dtype=np.float32)
    faiss.normalize_L2(matrix)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)
    limits, similarities, neighbors = index.range_search(matrix, threshold)
    union = _UnionFind(indices)
    best_scores: dict[int, float] = {}
    for local_left in range(len(indices)):
        for position in range(limits[local_left], limits[local_left + 1]):
            local_right = int(neighbors[position])
            if local_right == local_left:
                continue
            left = indices[local_left]
            right = indices[local_right]
            similarity = max(-1.0, min(1.0, float(similarities[position])))
            best_scores[left] = max(best_scores.get(left, float("-inf")), similarity)
            best_scores[right] = max(best_scores.get(right, float("-inf")), similarity)
            if local_right > local_left:
                union.union(left, right)
    groups = _as_groups("semantic", union.groups(), rank)
    return tuple(
        DuplicateGroup(
            kind=group.kind,
            group_key=(
                _stable_group_key(
                    "semantic",
                    tuple(stable_keys[index] for index in group.member_indices),
                )
                if stable_keys is not None
                else group.group_key
            ),
            member_indices=group.member_indices,
            representative_index=group.representative_index,
            member_scores=tuple(best_scores[index] for index in group.member_indices),
        )
        for group in groups
    )


def excluded_non_representatives(groups: tuple[DuplicateGroup, ...]) -> set[int]:
    return {
        index
        for group in groups
        for index in group.member_indices
        if index != group.representative_index
    }
