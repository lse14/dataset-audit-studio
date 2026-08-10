from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dataset_audit_studio.workspace.types import (
    ClusterListView,
    ClusterSampleListView,
    CoverageReportView,
    DirectoryListingView,
    FolderListView,
    ManualExclusionResult,
    RiskListView,
    RiskSampleDetailView,
    RiskSampleListView,
    TaskOverviewView,
)


class _ViewModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _CoverageViewModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class NamedCountResponse(_ViewModel):
    name: str
    count: int


class ReviewCountResponse(_ViewModel):
    category: str
    decision: str
    count: int


class TaskOverviewResponse(_ViewModel):
    samples_total: int
    samples_valid: int
    cluster_nodes: int
    leaf_clusters: int
    ready_artifacts: int
    evidence_codes: list[NamedCountResponse]
    review_counts: list[ReviewCountResponse]

    @classmethod
    def from_view(cls, view: TaskOverviewView) -> TaskOverviewResponse:
        return cls.model_validate(view)


class StyleEvidenceSummaryResponse(_CoverageViewModel):
    evidence_count: int
    missing_count: int
    finite_score_count: int
    score_status: str
    score_min: float | None
    score_median: float | None
    score_max: float | None
    review_only_count: int
    algorithm_version: str | None
    algorithm_versions: list[str]
    algorithm_version_status: str


class ScopeCoverageResponse(_CoverageViewModel):
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
    style_summary: StyleEvidenceSummaryResponse | None


class CoverageReportResponse(_CoverageViewModel):
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
    scopes: list[ScopeCoverageResponse]

    @classmethod
    def from_view(cls, view: CoverageReportView) -> CoverageReportResponse:
        return cls.model_validate(view)


class ClusterItemResponse(_ViewModel):
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


class ClusterListResponse(_ViewModel):
    items: list[ClusterItemResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: ClusterListView) -> ClusterListResponse:
        return cls.model_validate(view)


class FolderItemResponse(_ViewModel):
    folder_id: str
    display_name: str
    sample_count: int
    leaf_cluster_count: int
    risk_sample_count: int
    risk_evidence_count: int


class FolderListResponse(_ViewModel):
    items: list[FolderItemResponse]

    @classmethod
    def from_view(cls, view: FolderListView) -> FolderListResponse:
        return cls.model_validate(view)


class ClusterSampleResponse(_ViewModel):
    sample_id: str
    relative_path: str
    artist_scope: str
    score: float | None
    is_representative: bool
    manually_excluded: bool


class ClusterSampleListResponse(_ViewModel):
    items: list[ClusterSampleResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: ClusterSampleListView) -> ClusterSampleListResponse:
        return cls.model_validate(view)


class RiskEvidenceResponse(_ViewModel):
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


class RiskListResponse(_ViewModel):
    items: list[RiskEvidenceResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: RiskListView) -> RiskListResponse:
        return cls.model_validate(view)


class RiskSampleResponse(_ViewModel):
    sample_id: str
    relative_path: str
    artist_scope: str
    highest_severity: str
    evidence_count: int
    evidence_codes: list[str]
    manually_excluded: bool


class RiskSampleListResponse(_ViewModel):
    items: list[RiskSampleResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: RiskSampleListView) -> RiskSampleListResponse:
        return cls.model_validate(view)


class RiskSampleDetailResponse(_ViewModel):
    sample_id: str
    relative_path: str
    artist_scope: str
    manually_excluded: bool
    evidence: list[RiskEvidenceResponse]

    @classmethod
    def from_view(cls, view: RiskSampleDetailView) -> RiskSampleDetailResponse:
        return cls.model_validate(view)


class ManualExclusionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_ids: list[str] = Field(min_length=1, max_length=5000)
    excluded: bool
    context: dict[str, Any] = Field(default_factory=dict)


class ManualExclusionResponse(_ViewModel):
    selected: int
    changed: int
    excluded: bool

    @classmethod
    def from_result(cls, result: ManualExclusionResult) -> ManualExclusionResponse:
        return cls.model_validate(result)


class DirectoryEntryResponse(_ViewModel):
    name: str
    path: str


class DirectoryListingResponse(_ViewModel):
    current: str | None
    parent: str | None
    entries: list[DirectoryEntryResponse]

    @classmethod
    def from_view(cls, view: DirectoryListingView) -> DirectoryListingResponse:
        return cls.model_validate(view)


class DirectorySelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["source", "output"]
    initial_path: str | None = Field(default=None, max_length=32767)


class FileSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["model"]
    initial_path: str | None = Field(default=None, max_length=32767)


class DirectorySelectionResponse(BaseModel):
    path: str | None
    cancelled: bool
