from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NamedCountView:
    name: str
    count: int


@dataclass(frozen=True)
class ReviewCountView:
    category: str
    decision: str
    count: int


@dataclass(frozen=True)
class TaskOverviewView:
    samples_total: int
    samples_valid: int
    cluster_nodes: int
    leaf_clusters: int
    ready_artifacts: int
    evidence_codes: tuple[NamedCountView, ...]
    review_counts: tuple[ReviewCountView, ...]


@dataclass(frozen=True)
class ClusterItemView:
    cluster_id: str
    cluster_key: str
    scope_kind: str
    scope_id: str
    level: int
    size: int
    total_size: int
    folder_size: int
    representative_sample_id: str | None
    representative_path: str | None


@dataclass(frozen=True)
class ClusterListView:
    items: tuple[ClusterItemView, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True)
class FolderItemView:
    folder_id: str
    display_name: str
    sample_count: int
    leaf_cluster_count: int
    risk_sample_count: int
    risk_evidence_count: int


@dataclass(frozen=True)
class FolderListView:
    items: tuple[FolderItemView, ...]


@dataclass(frozen=True)
class ClusterSampleView:
    sample_id: str
    relative_path: str
    artist_scope: str
    score: float | None
    is_representative: bool
    manually_excluded: bool


@dataclass(frozen=True)
class ClusterSampleListView:
    items: tuple[ClusterSampleView, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True)
class RiskEvidenceView:
    evidence_id: str
    sample_id: str
    relative_path: str
    artist_scope: str
    code: str
    source: str
    value: Any
    threshold: Any | None
    value_number: float | None
    threshold_number: float | None
    severity: str
    review_only: bool
    bbox: list[float] | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RiskListView:
    items: tuple[RiskEvidenceView, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True)
class RiskSampleView:
    sample_id: str
    relative_path: str
    artist_scope: str
    highest_severity: str
    evidence_count: int
    evidence_codes: tuple[str, ...]
    manually_excluded: bool


@dataclass(frozen=True)
class RiskSampleListView:
    items: tuple[RiskSampleView, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True)
class RiskSampleDetailView:
    sample_id: str
    relative_path: str
    artist_scope: str
    manually_excluded: bool
    evidence: tuple[RiskEvidenceView, ...]


@dataclass(frozen=True)
class ManualExclusionResult:
    selected: int
    changed: int
    excluded: bool


@dataclass(frozen=True)
class DirectoryEntryView:
    name: str
    path: str


@dataclass(frozen=True)
class DirectoryListingView:
    current: str | None
    parent: str | None
    entries: tuple[DirectoryEntryView, ...]


@dataclass(frozen=True)
class StyleEvidenceSummaryView:
    evidence_count: int
    missing_count: int
    finite_score_count: int
    score_status: str
    score_min: float | None
    score_median: float | None
    score_max: float | None
    review_only_count: int
    algorithm_version: str | None
    algorithm_versions: tuple[str, ...]
    algorithm_version_status: str


@dataclass(frozen=True)
class ScopeCoverageView:
    scope_id: str
    broad_sample_count: int
    embedding_count: int
    missing_embedding_count: int
    embedding_status: str
    hierarchy_status: str
    leaf_coverage_status: str
    leaf_count: int | None
    single_leaf: bool | None
    leaf_assigned_count: int | None
    unassigned_count: int | None
    leaf_size_histogram: list[int] | None
    singleton_leaf_count: int | None
    singleton_sample_share: float | None
    largest_leaf_sample_share: float | None
    top_five_leaf_sample_share: float | None
    bottom_half_leaf_sample_share: float | None
    style_summary: StyleEvidenceSummaryView | None


@dataclass(frozen=True)
class CoverageReportView:
    schema_version: str
    status: str
    resolution: int
    profile: str | None
    coverage_type: str | None
    identity_assessment: str | None
    scope_count: int
    scope_size_histogram: list[int]
    scope_size_distribution_status: str
    single_leaf_scope_count: int | None
    single_leaf_scope_share: float | None
    single_leaf_scope_status: str
    scopes: tuple[ScopeCoverageView, ...]
