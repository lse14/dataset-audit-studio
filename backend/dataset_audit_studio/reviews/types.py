from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AIReviewCandidateView:
    sample_id: str
    relative_path: str
    artist_scope: str
    probability: float
    threshold: float
    reference_threshold: float
    decision: str
    decision_source: str
    decision_id: str | None
    decision_created_at: datetime | None


@dataclass(frozen=True)
class AIReviewListView:
    items: tuple[AIReviewCandidateView, ...]
    total: int
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int


@dataclass(frozen=True)
class ReviewSelection:
    sample_ids: tuple[str, ...] = ()
    artist_scope: str | None = None
    score_min: float | None = None
    score_max: float | None = None
    all_candidates: bool = False


@dataclass(frozen=True)
class ReviewDecisionResult:
    selected: int
    changed: int
    decision: str


@dataclass(frozen=True)
class CuratedReviewSelection:
    evidence_type: str
    sample_ids: tuple[str, ...] = ()
    artist_scope: str | None = None
    severity: str | None = None
    candidate_group: str | None = None


@dataclass(frozen=True)
class CuratedReviewCandidateView:
    sample_id: str
    relative_path: str
    artist_scope: str
    evidence_type: str
    reason_code: str
    score: float | None
    severity: str | None
    candidate_group: str | None
    decision: str
    decision_source: str
    decision_id: str | None
    decision_created_at: datetime | None


@dataclass(frozen=True)
class CuratedReviewListView:
    items: tuple[CuratedReviewCandidateView, ...]
    total: int
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int


@dataclass(frozen=True)
class StyleReviewCandidateView:
    sample_id: str
    relative_path: str
    artist_scope: str
    style_score: float
    threshold: float
    strong_outlier: bool
    reason: str | None
    decision: str
    decision_source: str
    decision_id: str | None
    decision_created_at: datetime | None


@dataclass(frozen=True)
class StyleReviewListView:
    items: tuple[StyleReviewCandidateView, ...]
    total: int
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int


@dataclass(frozen=True)
class StyleAuditCandidateView:
    sample_id: str
    relative_path: str
    artist_scope: str
    style_score: float
    threshold: float
    classification: str
    reason: str | None
    review_eligible: bool
    decision: str | None
    decision_source: str


@dataclass(frozen=True)
class StyleAuditListView:
    items: tuple[StyleAuditCandidateView, ...]
    total: int
    normal: int
    outlier: int
    strong_outlier: int
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int


@dataclass(frozen=True)
class AestheticAuditCandidateView:
    sample_id: str
    relative_path: str
    artist_scope: str
    score: float | None
    bucket: float | None
    reason_code: str | None
    review_eligible: bool
    decision: str | None
    decision_source: str


@dataclass(frozen=True)
class AestheticAuditListView:
    items: tuple[AestheticAuditCandidateView, ...]
    total: int
    bucket_counts: dict[str, int]
    invalid_counts: dict[str, int]
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int


@dataclass(frozen=True)
class DuplicateGroupMemberAuditView:
    sample_id: str
    relative_path: str
    artist_scope: str
    score: float | None
    decision: str | None
    decision_source: str
    review_eligible: bool
    pixel_area: int | None
    resolutions: tuple[int, ...]


@dataclass(frozen=True)
class DuplicateGroupAuditView:
    group_key: str
    evidence_type: str
    member_count: int
    pending: int
    approved_keep: int
    approved_exclude: int
    effective_retained_count: int
    members: tuple[DuplicateGroupMemberAuditView, ...]


@dataclass(frozen=True)
class DuplicateGroupAuditListView:
    items: tuple[DuplicateGroupAuditView, ...]
    total: int
    pending: int
    approved_keep: int
    approved_exclude: int
    unresolved: int
    offset: int
    limit: int


@dataclass(frozen=True)
class SAERepresentativeSampleView:
    sample_id: str
    relative_path: str


@dataclass(frozen=True)
class SAEFeatureView:
    feature_id: int
    threshold: float
    top_sample_ids: tuple[str, ...]
    representative_samples: tuple[SAERepresentativeSampleView, ...]


@dataclass(frozen=True)
class SAEFeatureListView:
    cache_key: str
    items: tuple[SAEFeatureView, ...]
    total: int
    offset: int
    limit: int
