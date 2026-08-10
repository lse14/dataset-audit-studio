from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataset_audit_studio.clustering.repository import EMBEDDING_ARTIFACT_KIND
from dataset_audit_studio.database.enums import ArtifactState, TaskStatus
from dataset_audit_studio.database.models import (
    Artifact,
    ClusterMembership,
    ClusterNode,
    Evidence,
    PhaseCheckpoint,
    Sample,
    Task,
    TaskConfig,
)
from dataset_audit_studio.jobs.profile import require_builtin_profile
from dataset_audit_studio.workspace.types import (
    CoverageReportView,
    ScopeCoverageView,
    StyleEvidenceSummaryView,
)

COVERAGE_REPORT_SCHEMA_VERSION = "coverage-report/v1"
_STYLE_CODE = "artist_style_score"
_STYLE_SOURCE = "artist_style_v1"


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def _opaque_sort(values: Iterable[str]) -> list[str]:
    return sorted(values, key=lambda value: (value.casefold(), value))


def _detect_profile(config: dict[str, Any]) -> str:
    require_builtin_profile(config)
    value = config.get("profile")
    if not isinstance(value, str):
        raise AssertionError("A built-in profile task must have a profile value")
    return value


def _latest_component_cursor(
    session: Session,
    *,
    task_id: str,
    config_hash: str,
    component_id: str,
) -> dict[str, Any] | None:
    rows = session.scalars(
        select(PhaseCheckpoint)
        .where(
            PhaseCheckpoint.task_id == task_id,
            PhaseCheckpoint.phase == TaskStatus.SEMANTIC_CLUSTERING.value,
            PhaseCheckpoint.config_hash == config_hash,
        )
        .order_by(PhaseCheckpoint.batch_index.desc(), PhaseCheckpoint.id.desc())
    ).all()
    for row in rows:
        cursor = row.cursor_json
        if (
            cursor.get("modular_clustering") is True
            and cursor.get("component_id") == component_id
            and cursor.get("component_complete") is True
        ):
            return dict(cursor)
    return None


def _artifact_keys(cursor: dict[str, Any] | None) -> tuple[str, ...] | None:
    if cursor is None:
        return None
    values = cursor.get("artifact_keys")
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return None
    return tuple(values)


def _active_embedding_ids(
    session: Session,
    *,
    task_id: str,
    artifact_keys: tuple[str, ...] | None,
) -> tuple[set[str], str]:
    if artifact_keys is None or len(set(artifact_keys)) != len(artifact_keys):
        return set(), "unavailable"

    artifacts = {
        row.cache_key: row
        for row in session.scalars(
            select(Artifact).where(
                Artifact.task_id == task_id,
                Artifact.kind == EMBEDDING_ARTIFACT_KIND,
                Artifact.state == ArtifactState.READY.value,
            )
        ).all()
    }
    if any(key not in artifacts for key in artifact_keys):
        return set(), "unavailable"

    sample_ids: set[str] = set()
    for key in artifact_keys:
        values = artifacts[key].metadata_json.get("sample_ids")
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return set(), "unavailable"
        sample_ids.update(values)
    return sample_ids, "available"


def _style_summary(rows: list[Evidence], broad_sample_count: int) -> StyleEvidenceSummaryView:
    by_sample: dict[str, Evidence] = {}
    for row in sorted(rows, key=lambda item: (item.created_at, item.id)):
        by_sample[row.sample_id] = row
    evidence = tuple(by_sample.values())
    finite_scores = sorted(
        float(row.value_number)
        for row in evidence
        if row.value_number is not None and math.isfinite(row.value_number)
    )
    versions = tuple(_opaque_sort({row.algorithm_version for row in evidence}))
    if not versions:
        version_status = "unavailable"
        algorithm_version = None
    elif len(versions) == 1:
        version_status = "available"
        algorithm_version = versions[0]
    else:
        version_status = "mixed"
        algorithm_version = None
    return StyleEvidenceSummaryView(
        evidence_count=len(evidence),
        missing_count=max(0, broad_sample_count - len(evidence)),
        finite_score_count=len(finite_scores),
        score_status="available" if finite_scores else "unavailable",
        score_min=finite_scores[0] if finite_scores else None,
        score_median=statistics.median(finite_scores) if finite_scores else None,
        score_max=finite_scores[-1] if finite_scores else None,
        review_only_count=sum(1 for row in evidence if row.review_only),
        algorithm_version=algorithm_version,
        algorithm_versions=versions,
        algorithm_version_status=version_status,
    )


def _scope_coverage(
    *,
    scope_id: str,
    broad_ids: set[str],
    embedding_ids: set[str],
    embedding_status: str,
    hierarchy_status: str,
    leaf_members: dict[str, set[str]],
    hierarchy_inconsistent: bool,
    style_rows: list[Evidence] | None,
) -> ScopeCoverageView:
    broad_sample_count = len(broad_ids)
    embedding_count = len(broad_ids.intersection(embedding_ids))
    missing_embedding_count = broad_sample_count - embedding_count

    if hierarchy_status != "available" or hierarchy_inconsistent:
        status = "inconsistent" if hierarchy_inconsistent else "unavailable"
        return ScopeCoverageView(
            scope_id=scope_id,
            broad_sample_count=broad_sample_count,
            embedding_count=embedding_count,
            missing_embedding_count=missing_embedding_count,
            embedding_status=embedding_status,
            hierarchy_status=status,
            leaf_coverage_status=status,
            leaf_count=None,
            single_leaf=None,
            leaf_assigned_count=None,
            unassigned_count=None,
            leaf_size_histogram=None,
            singleton_leaf_count=None,
            singleton_sample_share=None,
            largest_leaf_sample_share=None,
            top_five_leaf_sample_share=None,
            bottom_half_leaf_sample_share=None,
            style_summary=(
                _style_summary(style_rows, broad_sample_count) if style_rows is not None else None
            ),
        )

    leaf_sizes = sorted(
        len(members.intersection(broad_ids))
        for members in leaf_members.values()
        if members.intersection(broad_ids)
    )
    assigned_ids = set().union(*leaf_members.values()) if leaf_members else set()
    assigned_ids.intersection_update(broad_ids)
    leaf_assigned_count = len(assigned_ids)
    unassigned_count = broad_sample_count - leaf_assigned_count
    leaf_count = len(leaf_sizes)

    if broad_sample_count == 0:
        leaf_coverage_status = "empty"
    elif leaf_assigned_count == 0:
        leaf_coverage_status = "no_leaf_assignments"
    else:
        leaf_coverage_status = "available"

    singleton_leaf_count = sum(size == 1 for size in leaf_sizes)
    largest_leaf_size = leaf_sizes[-1] if leaf_sizes else 0
    top_five_size = sum(leaf_sizes[-5:])
    bottom_half_size = sum(leaf_sizes[: math.ceil(leaf_count / 2)])

    return ScopeCoverageView(
        scope_id=scope_id,
        broad_sample_count=broad_sample_count,
        embedding_count=embedding_count,
        missing_embedding_count=missing_embedding_count,
        embedding_status=embedding_status,
        hierarchy_status="available",
        leaf_coverage_status=leaf_coverage_status,
        leaf_count=leaf_count,
        single_leaf=leaf_count == 1,
        leaf_assigned_count=leaf_assigned_count,
        unassigned_count=unassigned_count,
        leaf_size_histogram=leaf_sizes,
        singleton_leaf_count=singleton_leaf_count,
        singleton_sample_share=_safe_ratio(singleton_leaf_count, leaf_assigned_count),
        largest_leaf_sample_share=_safe_ratio(largest_leaf_size, leaf_assigned_count),
        top_five_leaf_sample_share=_safe_ratio(top_five_size, leaf_assigned_count),
        bottom_half_leaf_sample_share=_safe_ratio(bottom_half_size, leaf_assigned_count),
        style_summary=(
            _style_summary(style_rows, broad_sample_count) if style_rows is not None else None
        ),
    )


def compute_coverage_report(
    session: Session,
    task: Task,
    resolution: int,
) -> CoverageReportView:
    current_config = session.scalar(
        select(TaskConfig).where(
            TaskConfig.task_id == task.id,
            TaskConfig.revision == task.current_config_revision,
        )
    )
    if current_config is None:
        raise RuntimeError("Task has no current configuration")

    profile = _detect_profile(current_config.config_json)

    broad_rows = session.execute(
        select(Sample.id, Sample.artist_scope, Sample.display_width, Sample.display_height,
               Sample.encoded_width, Sample.encoded_height)
        .where(Sample.task_id == task.id, Sample.scan_state == "valid")
        .order_by(Sample.id)
    )
    broad_ids: set[str] = set()
    broad_scope_by_id: dict[str, str] = {}
    for (
        sample_id,
        scope_id,
        display_width,
        display_height,
        encoded_width,
        encoded_height,
    ) in broad_rows:
        width = display_width or encoded_width
        height = display_height or encoded_height
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or width * height < resolution * resolution
        ):
            continue
        broad_ids.add(sample_id)
        broad_scope_by_id[sample_id] = scope_id

    if profile == "general":
        scope_groups = {"__global__": broad_ids}
    else:
        scope_groups = {
            scope_id: set()
            for scope_id in session.scalars(
                select(Sample.artist_scope)
                .where(Sample.task_id == task.id)
                .distinct()
            ).all()
        }
        for sample_id, scope_id in broad_scope_by_id.items():
            scope_groups.setdefault(scope_id, set()).add(sample_id)

    embedding_cursor = _latest_component_cursor(
        session,
        task_id=task.id,
        config_hash=current_config.config_hash,
        component_id="embedding.semantic",
    )
    embedding_keys = _artifact_keys(embedding_cursor)
    embedding_ids, embedding_status = _active_embedding_ids(
        session,
        task_id=task.id,
        artifact_keys=embedding_keys,
    )

    hierarchy_cursor = _latest_component_cursor(
        session,
        task_id=task.id,
        config_hash=current_config.config_hash,
        component_id="cluster.hierarchy",
    )
    hierarchy_keys = _artifact_keys(hierarchy_cursor)
    hierarchy_hash = hierarchy_cursor.get("hierarchy_hash") if hierarchy_cursor else None
    hierarchy_status = (
        "available"
        if (
            embedding_status == "available"
            and hierarchy_cursor is not None
            and hierarchy_cursor.get("clusters_prepared") is True
            and hierarchy_keys == embedding_keys
            and isinstance(hierarchy_hash, str)
        )
        else "unavailable"
    )

    leaf_members: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    inconsistent_scopes: set[str] = set()
    assigned_leaf_by_sample: dict[tuple[str, str], str] = {}
    if hierarchy_status == "available":
        leaf_rows = session.execute(
            select(
                ClusterNode.cluster_key,
                ClusterNode.scope_id,
                ClusterNode.scope_kind,
                ClusterMembership.sample_id,
            )
            .join(
                ClusterMembership,
                (ClusterMembership.cluster_id == ClusterNode.id)
                & (ClusterMembership.task_id == task.id),
            )
            .where(
                ClusterNode.task_id == task.id,
                ClusterNode.metadata_json["is_leaf"].as_boolean().is_(True),
                ClusterNode.metadata_json["hierarchy_config_hash"].as_string() == hierarchy_hash,
            )
            .order_by(
                ClusterNode.scope_id,
                ClusterNode.cluster_key,
                ClusterMembership.sample_id,
            )
        )
        for cluster_key, node_scope_id, scope_kind, sample_id in leaf_rows:
            if sample_id not in broad_ids:
                continue
            if profile == "general":
                if scope_kind != "global":
                    continue
                report_scope_id = "__global__"
            else:
                if scope_kind not in {"artist", "concept"}:
                    continue
                report_scope_id = broad_scope_by_id.get(sample_id)
                if report_scope_id is None or node_scope_id != report_scope_id:
                    if report_scope_id is not None:
                        inconsistent_scopes.add(report_scope_id)
                    continue
            identity = (report_scope_id, sample_id)
            prior_leaf = assigned_leaf_by_sample.get(identity)
            if prior_leaf is not None and prior_leaf != cluster_key:
                inconsistent_scopes.add(report_scope_id)
                continue
            assigned_leaf_by_sample[identity] = cluster_key
            leaf_members[report_scope_id][cluster_key].add(sample_id)

    style_rows_by_scope: dict[str, list[Evidence]] = defaultdict(list)
    if profile == "artist_concept" and broad_ids:
        for row in session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task.id,
                Evidence.code == _STYLE_CODE,
                Evidence.source == _STYLE_SOURCE,
            )
            .order_by(Evidence.created_at, Evidence.id)
        ):
            scope_id = broad_scope_by_id.get(row.sample_id)
            if scope_id is not None:
                style_rows_by_scope[scope_id].append(row)

    scopes = tuple(
        _scope_coverage(
            scope_id=scope_id,
            broad_ids=scope_groups[scope_id],
            embedding_ids=embedding_ids,
            embedding_status=embedding_status,
            hierarchy_status=hierarchy_status,
            leaf_members=leaf_members.get(scope_id, {}),
            hierarchy_inconsistent=scope_id in inconsistent_scopes,
            style_rows=(style_rows_by_scope[scope_id] if profile == "artist_concept" else None),
        )
        for scope_id in _opaque_sort(scope_groups)
    )

    hierarchy_available = hierarchy_status == "available" and not inconsistent_scopes
    if not scopes:
        single_leaf_scope_count = 0 if hierarchy_available else None
        single_leaf_scope_share = None
        single_leaf_scope_status = "empty" if hierarchy_available else "unavailable"
    elif not hierarchy_available:
        single_leaf_scope_count = None
        single_leaf_scope_share = None
        single_leaf_scope_status = "unavailable"
    else:
        single_leaf_scope_count = sum(scope.single_leaf is True for scope in scopes)
        single_leaf_scope_share = _safe_ratio(single_leaf_scope_count, len(scopes))
        single_leaf_scope_status = "available"

    if profile == "character_concept":
        coverage_type = "visual_semantic_coverage_proxy"
        identity_assessment = "not_performed"
    elif profile == "general":
        coverage_type = "global_semantic_coverage"
        identity_assessment = None
    else:
        coverage_type = "concept_scope_coverage"
        identity_assessment = None

    return CoverageReportView(
        schema_version=COVERAGE_REPORT_SCHEMA_VERSION,
        status="ready",
        resolution=resolution,
        profile=profile,
        coverage_type=coverage_type,
        identity_assessment=identity_assessment,
        scope_count=len(scopes),
        scope_size_histogram=sorted(scope.broad_sample_count for scope in scopes),
        scope_size_distribution_status="available" if scopes else "empty",
        single_leaf_scope_count=single_leaf_scope_count,
        single_leaf_scope_share=single_leaf_scope_share,
        single_leaf_scope_status=single_leaf_scope_status,
        scopes=scopes,
    )
