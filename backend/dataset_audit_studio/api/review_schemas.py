from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataset_audit_studio.database.enums import ReviewState
from dataset_audit_studio.reviews.types import (
    AestheticAuditCandidateView,
    AestheticAuditListView,
    AIReviewCandidateView,
    AIReviewListView,
    CuratedReviewCandidateView,
    CuratedReviewListView,
    CuratedReviewSelection,
    DuplicateGroupAuditListView,
    DuplicateGroupAuditView,
    DuplicateGroupMemberAuditView,
    ReviewDecisionResult,
    ReviewSelection,
    SAEFeatureListView,
    SAEFeatureView,
    SAERepresentativeSampleView,
    StyleAuditCandidateView,
    StyleAuditListView,
    StyleReviewCandidateView,
    StyleReviewListView,
)

HumanReviewDecision = Literal["approved_keep", "approved_exclude"]


class AIReviewCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    relative_path: str
    artist_scope: str
    probability: float
    threshold: float
    reference_threshold: float
    decision: ReviewState
    decision_source: str
    decision_id: str | None
    decision_created_at: datetime | None

    @classmethod
    def from_view(cls, view: AIReviewCandidateView) -> AIReviewCandidateResponse:
        return cls.model_validate(view.__dict__)


class AIReviewListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AIReviewCandidateResponse]
    total: int
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: AIReviewListView) -> AIReviewListResponse:
        return cls(
            items=[AIReviewCandidateResponse.from_view(item) for item in view.items],
            total=view.total,
            pending=view.pending,
            approved_keep=view.approved_keep,
            approved_exclude=view.approved_exclude,
            offset=view.offset,
            limit=view.limit,
        )


class AIReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: HumanReviewDecision
    sample_ids: list[str] = Field(default_factory=list, max_length=5000)
    artist_scope: str | None = Field(default=None, min_length=1, max_length=1000)
    score_min: float | None = Field(default=None, ge=0.0, le=1.0)
    score_max: float | None = Field(default=None, ge=0.0, le=1.0)
    all_candidates: bool = False

    @model_validator(mode="after")
    def validate_selector(self) -> AIReviewDecisionRequest:
        if not (
            self.sample_ids
            or self.artist_scope is not None
            or self.score_min is not None
            or self.score_max is not None
            or self.all_candidates
        ):
            raise ValueError("An explicit review selector is required")
        if (
            self.score_min is not None
            and self.score_max is not None
            and self.score_min > self.score_max
        ):
            raise ValueError("score_min cannot exceed score_max")
        return self

    def to_selection(self) -> ReviewSelection:
        return ReviewSelection(
            sample_ids=tuple(dict.fromkeys(self.sample_ids)),
            artist_scope=self.artist_scope,
            score_min=self.score_min,
            score_max=self.score_max,
            all_candidates=self.all_candidates,
        )

    def to_decision(self) -> ReviewState:
        return ReviewState(self.decision)


class AIReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: int
    changed: int
    decision: ReviewState

    @classmethod
    def from_result(cls, result: ReviewDecisionResult) -> AIReviewDecisionResponse:
        return cls.model_validate(result.__dict__)


class CuratedReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_type: Literal[
        "aesthetic",
        "risk",
        "style_outlier",
        "duplicate",
        "exact_duplicate",
        "visual_duplicate",
        "semantic_duplicate",
    ]
    decision: HumanReviewDecision
    sample_ids: list[str] = Field(default_factory=list, max_length=5000)
    artist_scope: str | None = Field(default=None, min_length=1, max_length=1000)
    severity: Literal["info", "low", "medium", "high", "fatal"] | None = None
    candidate_group: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_selector(self) -> CuratedReviewDecisionRequest:
        if not (self.sample_ids or self.artist_scope or self.severity or self.candidate_group):
            raise ValueError("An explicit curated review selector is required")
        return self

    def to_selection(self) -> CuratedReviewSelection:
        return CuratedReviewSelection(
            evidence_type=self.evidence_type,
            sample_ids=tuple(dict.fromkeys(self.sample_ids)),
            artist_scope=self.artist_scope,
            severity=self.severity,
            candidate_group=self.candidate_group,
        )

    def to_decision(self) -> ReviewState:
        return ReviewState(self.decision)


class CuratedReviewCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    relative_path: str
    artist_scope: str
    evidence_type: str
    reason_code: str
    score: float | None
    severity: str | None
    candidate_group: str | None
    decision: ReviewState
    decision_source: str
    decision_id: str | None
    decision_created_at: datetime | None

    @classmethod
    def from_view(
        cls,
        view: CuratedReviewCandidateView,
    ) -> CuratedReviewCandidateResponse:
        return cls.model_validate(view.__dict__)


class CuratedReviewListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CuratedReviewCandidateResponse]
    total: int
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: CuratedReviewListView) -> CuratedReviewListResponse:
        return cls(
            items=[CuratedReviewCandidateResponse.from_view(item) for item in view.items],
            total=view.total,
            pending=view.pending,
            approved_keep=view.approved_keep,
            approved_exclude=view.approved_exclude,
            offset=view.offset,
            limit=view.limit,
        )


class StyleReviewCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    relative_path: str
    artist_scope: str
    style_score: float
    threshold: float
    strong_outlier: bool
    reason: str | None
    decision: ReviewState
    decision_source: str
    decision_id: str | None
    decision_created_at: datetime | None

    @classmethod
    def from_view(cls, view: StyleReviewCandidateView) -> StyleReviewCandidateResponse:
        return cls.model_validate(view.__dict__)


class StyleReviewListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StyleReviewCandidateResponse]
    total: int
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: StyleReviewListView) -> StyleReviewListResponse:
        return cls(
            items=[StyleReviewCandidateResponse.from_view(item) for item in view.items],
            total=view.total,
            pending=view.pending,
            approved_keep=view.approved_keep,
            approved_exclude=view.approved_exclude,
            offset=view.offset,
            limit=view.limit,
        )


class StyleReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: HumanReviewDecision
    sample_ids: list[str] = Field(default_factory=list, max_length=5000)
    artist_scope: str | None = Field(default=None, min_length=1, max_length=1000)
    score_min: float | None = Field(default=None, ge=0.0, le=100.0)
    score_max: float | None = Field(default=None, ge=0.0, le=100.0)
    all_candidates: bool = False

    @model_validator(mode="after")
    def validate_selector(self) -> StyleReviewDecisionRequest:
        if not (
            self.sample_ids
            or self.artist_scope is not None
            or self.score_min is not None
            or self.score_max is not None
            or self.all_candidates
        ):
            raise ValueError("An explicit review selector is required")
        if (
            self.score_min is not None
            and self.score_max is not None
            and self.score_min > self.score_max
        ):
            raise ValueError("score_min cannot exceed score_max")
        return self

    def to_selection(self) -> ReviewSelection:
        return ReviewSelection(
            sample_ids=tuple(dict.fromkeys(self.sample_ids)),
            artist_scope=self.artist_scope,
            score_min=self.score_min,
            score_max=self.score_max,
            all_candidates=self.all_candidates,
        )

    def to_decision(self) -> ReviewState:
        return ReviewState(self.decision)


class StyleAuditCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    relative_path: str
    artist_scope: str
    style_score: float
    threshold: float
    classification: Literal["normal", "outlier", "strong_outlier"]
    reason: str | None
    review_eligible: bool
    decision: ReviewState | None
    decision_source: str

    @classmethod
    def from_view(cls, view: StyleAuditCandidateView) -> StyleAuditCandidateResponse:
        return cls.model_validate(view.__dict__)


class StyleAuditListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StyleAuditCandidateResponse]
    total: int
    normal: int
    outlier: int
    strong_outlier: int
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: StyleAuditListView) -> StyleAuditListResponse:
        return cls(
            items=[StyleAuditCandidateResponse.from_view(item) for item in view.items],
            total=view.total,
            normal=view.normal,
            outlier=view.outlier,
            strong_outlier=view.strong_outlier,
            pending=view.pending,
            approved_keep=view.approved_keep,
            approved_exclude=view.approved_exclude,
            offset=view.offset,
            limit=view.limit,
        )


class AestheticAuditCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    relative_path: str
    artist_scope: str
    score: float | None
    bucket: float | None
    reason_code: Literal[
        "missing",
        "non_finite",
        "out_of_range",
        "provenance_mismatch",
        "ambiguous",
    ] | None
    review_eligible: bool
    decision: ReviewState | None
    decision_source: str

    @classmethod
    def from_view(cls, view: AestheticAuditCandidateView) -> AestheticAuditCandidateResponse:
        return cls.model_validate(view.__dict__)


class AestheticAuditListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AestheticAuditCandidateResponse]
    total: int
    bucket_counts: dict[str, int]
    invalid_counts: dict[str, int]
    pending: int
    approved_keep: int
    approved_exclude: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: AestheticAuditListView) -> AestheticAuditListResponse:
        return cls(
            items=[AestheticAuditCandidateResponse.from_view(item) for item in view.items],
            total=view.total,
            bucket_counts=view.bucket_counts,
            invalid_counts=view.invalid_counts,
            pending=view.pending,
            approved_keep=view.approved_keep,
            approved_exclude=view.approved_exclude,
            offset=view.offset,
            limit=view.limit,
        )


class DuplicateGroupMemberAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    relative_path: str
    artist_scope: str
    score: float | None
    decision: ReviewState | None
    decision_source: str
    review_eligible: bool
    pixel_area: int | None
    resolutions: list[int]

    @classmethod
    def from_view(
        cls, view: DuplicateGroupMemberAuditView
    ) -> DuplicateGroupMemberAuditResponse:
        return cls(
            sample_id=view.sample_id,
            relative_path=view.relative_path,
            artist_scope=view.artist_scope,
            score=view.score,
            decision=view.decision,
            decision_source=view.decision_source,
            review_eligible=view.review_eligible,
            pixel_area=view.pixel_area,
            resolutions=list(view.resolutions),
        )


class DuplicateGroupAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: str
    evidence_type: str
    member_count: int
    pending: int
    approved_keep: int
    approved_exclude: int
    effective_retained_count: int
    members: list[DuplicateGroupMemberAuditResponse]

    @classmethod
    def from_view(cls, view: DuplicateGroupAuditView) -> DuplicateGroupAuditResponse:
        return cls(
            group_key=view.group_key,
            evidence_type=view.evidence_type,
            member_count=view.member_count,
            pending=view.pending,
            approved_keep=view.approved_keep,
            approved_exclude=view.approved_exclude,
            effective_retained_count=view.effective_retained_count,
            members=[DuplicateGroupMemberAuditResponse.from_view(item) for item in view.members],
        )


class DuplicateGroupAuditListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DuplicateGroupAuditResponse]
    total: int
    pending: int
    approved_keep: int
    approved_exclude: int
    unresolved: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: DuplicateGroupAuditListView) -> DuplicateGroupAuditListResponse:
        return cls(
            items=[DuplicateGroupAuditResponse.from_view(item) for item in view.items],
            total=view.total,
            pending=view.pending,
            approved_keep=view.approved_keep,
            approved_exclude=view.approved_exclude,
            unresolved=view.unresolved,
            offset=view.offset,
            limit=view.limit,
        )


class SAERepresentativeSampleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str
    relative_path: str

    @classmethod
    def from_view(
        cls,
        view: SAERepresentativeSampleView,
    ) -> SAERepresentativeSampleResponse:
        return cls.model_validate(view.__dict__)


class SAEFeatureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: int
    threshold: float
    top_sample_ids: list[str]
    representative_samples: list[SAERepresentativeSampleResponse]

    @classmethod
    def from_view(cls, view: SAEFeatureView) -> SAEFeatureResponse:
        return cls(
            feature_id=view.feature_id,
            threshold=view.threshold,
            top_sample_ids=list(view.top_sample_ids),
            representative_samples=[
                SAERepresentativeSampleResponse.from_view(sample)
                for sample in view.representative_samples
            ],
        )


class SAEFeatureListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_key: str
    items: list[SAEFeatureResponse]
    total: int
    offset: int
    limit: int

    @classmethod
    def from_view(cls, view: SAEFeatureListView) -> SAEFeatureListResponse:
        return cls(
            cache_key=view.cache_key,
            items=[SAEFeatureResponse.from_view(item) for item in view.items],
            total=view.total,
            offset=view.offset,
            limit=view.limit,
        )
