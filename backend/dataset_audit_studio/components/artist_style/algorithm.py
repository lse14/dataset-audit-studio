from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.components.artist_style.contracts import StyleAssessment


@dataclass(frozen=True)
class _FamilyScores:
    average_similarity: np.ndarray
    average_score: np.ndarray
    centroid_distance: np.ndarray
    centroid_score: np.ndarray
    threshold: float


def normalize_rows(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.ndim != 2:
        raise ValueError("Feature arrays must be two-dimensional")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0):
        raise ValueError("Feature arrays contain non-finite or zero-length rows")
    if np.allclose(norms, 1.0, atol=1e-5, rtol=0.0):
        return array
    return array / norms


def _higher_better(values: np.ndarray, floor: float, ceiling: float) -> np.ndarray:
    if ceiling <= floor:
        return np.full(values.shape, 100.0, dtype=np.float32)
    return np.clip((values - floor) / (ceiling - floor) * 100.0, 0.0, 100.0)


def _lower_better(values: np.ndarray, best: float, worst: float) -> np.ndarray:
    if worst <= best:
        return np.full(values.shape, 100.0, dtype=np.float32)
    return np.clip((worst - values) / (worst - best) * 100.0, 0.0, 100.0)


def _average_similarity(features: np.ndarray, active: np.ndarray) -> np.ndarray:
    reference = features[active]
    count = reference.shape[0]
    if count == 0:
        raise ValueError("Style analysis requires at least one active sample")
    summed = reference.sum(axis=0, dtype=np.float64)
    similarities = features.astype(np.float64, copy=False) @ summed
    similarities /= count
    if count > 1:
        active_indices = np.flatnonzero(active)
        self_similarity = np.einsum(
            "ij,ij->i", features[active], features[active], dtype=np.float64
        )
        similarities[active_indices] = (
            similarities[active_indices] * count - self_similarity
        ) / (count - 1)
    return similarities.astype(np.float32)


def _family_scores(
    features: np.ndarray,
    active: np.ndarray,
    *,
    sigma: float,
) -> _FamilyScores:
    similarities = _average_similarity(features, active)
    reference_values = similarities[active]
    mean = float(reference_values.mean(dtype=np.float64))
    std = float(reference_values.std(dtype=np.float64))
    threshold = max(0.0, mean - sigma * std)
    floor = max(0.0, mean - max(0.18, std * 2.0))
    average_score = _higher_better(similarities, floor, max(mean, floor + 1e-6))

    centroid = features[active].mean(axis=0, dtype=np.float64)
    row_norms = np.einsum("ij,ij->i", features, features, dtype=np.float64)
    distances = row_norms - 2.0 * (features @ centroid) + float(centroid @ centroid)
    distances = np.maximum(distances, 0.0)
    dispersion = float(distances[active].mean(dtype=np.float64))
    centroid_score = _lower_better(
        distances.astype(np.float32),
        0.0,
        max(dispersion * 2.0, 1e-6),
    )
    return _FamilyScores(
        average_similarity=similarities,
        average_score=average_score,
        centroid_distance=distances.astype(np.float32),
        centroid_score=centroid_score,
        threshold=threshold,
    )


def _neutral_family_scores(count: int) -> _FamilyScores:
    """Keep the persisted LSNet fields valid without using LSNet as evidence."""
    return _FamilyScores(
        average_similarity=np.ones(count, dtype=np.float32),
        average_score=np.full(count, 100.0, dtype=np.float32),
        centroid_distance=np.zeros(count, dtype=np.float32),
        centroid_score=np.full(count, 100.0, dtype=np.float32),
        threshold=0.0,
    )


def _dino_guardrail(
    similarities: np.ndarray,
    active: np.ndarray,
    *,
    threshold: float,
) -> np.ndarray:
    reference_values = similarities[active]
    std = float(reference_values.std(dtype=np.float64))
    floor = max(0.0, threshold - max(0.10, std * 1.5))
    scores = _higher_better(similarities, floor, max(threshold, floor + 1e-6))
    scores[similarities >= threshold] = 100.0
    return scores


def analyze_artist_scope(
    sample_ids: tuple[str, ...],
    lsnet_features: np.ndarray,
    gram_features: np.ndarray,
    dino_features: np.ndarray,
    color_histograms: np.ndarray,
    config: StyleConfig,
) -> tuple[StyleAssessment, ...]:
    count = len(sample_ids)
    if count == 0:
        return ()
    lsnet = normalize_rows(lsnet_features)
    gram = normalize_rows(gram_features)
    dino = normalize_rows(dino_features)
    colors = np.asarray(color_histograms, dtype=np.float32)
    if (
        lsnet.shape[0] != count
        or gram.shape[0] != count
        or dino.shape[0] != count
        or colors.shape[0] != count
    ):
        raise ValueError("Style feature rows do not match sample ids")
    if colors.ndim != 2 or np.any(~np.isfinite(colors)):
        raise ValueError("Color histogram features are invalid")

    active = np.ones(count, dtype=bool)
    removed_iteration = np.full(count, -1, dtype=np.int16)
    small_scope = count < config.minimum_scope_size

    for iteration in range(1, config.max_iterations + 1):
        lsnet_scores = (
            _family_scores(lsnet, active, sigma=config.outlier_sigma)
            if config.lsnet_weight > 0.0
            else _neutral_family_scores(count)
        )
        gram_scores = (
            _family_scores(gram, active, sigma=config.outlier_sigma)
            if config.gram_weight > 0.0
            else _neutral_family_scores(count)
        )
        dino_scores = (
            _family_scores(dino, active, sigma=config.outlier_sigma)
            if config.dino_weight > 0.0
            else _neutral_family_scores(count)
        )
        guardrail = (
            _dino_guardrail(
                dino_scores.average_similarity,
                active,
                threshold=dino_scores.threshold,
            )
            if config.dino_weight > 0.0
            else np.full(count, 100.0, dtype=np.float32)
        )
        gram_style = (
            gram_scores.average_score * config.gram_average_weight
            + gram_scores.centroid_score * config.gram_centroid_weight
        )
        combined = (
            lsnet_scores.average_score * config.lsnet_weight
            + gram_style * config.gram_weight
            + guardrail * config.dino_weight
        )
        candidates = (
            active
            & (
                (
                    (config.lsnet_weight > 0.0)
                    & (lsnet_scores.average_similarity < lsnet_scores.threshold)
                )
                | (
                    (config.gram_weight > 0.0)
                    & (gram_scores.average_similarity < gram_scores.threshold)
                )
            )
            & (combined < config.minimum_style_score)
        )
        if small_scope or not np.any(candidates):
            break
        remaining = int(active.sum() - candidates.sum())
        if remaining < 2:
            candidate_indices = np.flatnonzero(candidates)
            order = sorted(candidate_indices, key=lambda index: (combined[index], index))
            removable = max(0, int(active.sum()) - 2)
            candidates[:] = False
            candidates[order[:removable]] = True
        if not np.any(candidates):
            break
        active[candidates] = False
        removed_iteration[candidates] = iteration

    lsnet_scores = (
        _family_scores(lsnet, active, sigma=config.outlier_sigma)
        if config.lsnet_weight > 0.0
        else _neutral_family_scores(count)
    )
    gram_scores = (
        _family_scores(gram, active, sigma=config.outlier_sigma)
        if config.gram_weight > 0.0
        else _neutral_family_scores(count)
    )
    dino_scores = (
        _family_scores(dino, active, sigma=config.outlier_sigma)
        if config.dino_weight > 0.0
        else _neutral_family_scores(count)
    )
    guardrail = (
        _dino_guardrail(
            dino_scores.average_similarity,
            active,
            threshold=dino_scores.threshold,
        )
        if config.dino_weight > 0.0
        else np.full(count, 100.0, dtype=np.float32)
    )
    gram_style = (
        gram_scores.average_score * config.gram_average_weight
        + gram_scores.centroid_score * config.gram_centroid_weight
    )
    combined = (
        lsnet_scores.average_score * config.lsnet_weight
        + gram_style * config.gram_weight
        + guardrail * config.dino_weight
    )
    color_center = colors[active].mean(axis=0, dtype=np.float64)
    color_l1 = np.abs(colors.astype(np.float64, copy=False) - color_center).sum(axis=1)
    candidate_outlier = (
        (
            (
                (config.lsnet_weight > 0.0)
                & (lsnet_scores.average_similarity < lsnet_scores.threshold)
            )
            | (
                (config.gram_weight > 0.0)
                & (gram_scores.average_similarity < gram_scores.threshold)
            )
        )
        & (combined < config.minimum_style_score)
    )

    assessments: list[StyleAssessment] = []
    for index, sample_id in enumerate(sample_ids):
        strong_outlier = not bool(active[index])
        review_required = strong_outlier or (small_scope and bool(candidate_outlier[index]))
        reason = None
        if strong_outlier:
            lsnet_outlier = lsnet_scores.average_similarity[index] < lsnet_scores.threshold
            gram_outlier = (
                config.gram_weight > 0.0
                and gram_scores.average_similarity[index] < gram_scores.threshold
            )
            reason = (
                "lsnet_and_gram_similarity_below_scope_threshold"
                if lsnet_outlier and gram_outlier
                else "lsnet_similarity_below_scope_threshold"
                if lsnet_outlier
                else "gram_similarity_below_scope_threshold"
            )
        elif small_scope and candidate_outlier[index]:
            reason = "small_scope_review"
        assessments.append(
            StyleAssessment(
                sample_id=sample_id,
                style_score=float(combined[index]),
                lsnet_average_similarity=float(lsnet_scores.average_similarity[index]),
                lsnet_average_score=float(lsnet_scores.average_score[index]),
                gram_average_similarity=float(gram_scores.average_similarity[index]),
                gram_average_score=float(gram_scores.average_score[index]),
                gram_centroid_distance=float(gram_scores.centroid_distance[index]),
                gram_centroid_score=float(gram_scores.centroid_score[index]),
                dino_average_similarity=float(dino_scores.average_similarity[index]),
                dino_guardrail_score=float(guardrail[index]),
                color_histogram_l1=float(color_l1[index]),
                core_member=bool(active[index]),
                strong_outlier=strong_outlier,
                review_required=review_required,
                outlier_reason=reason,
                iteration_removed=(
                    int(removed_iteration[index]) if removed_iteration[index] >= 0 else None
                ),
                lsnet_threshold=lsnet_scores.threshold,
                gram_threshold=gram_scores.threshold,
                dino_threshold=dino_scores.threshold,
            )
        )
    return tuple(assessments)
