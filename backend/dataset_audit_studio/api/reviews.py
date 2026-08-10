from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from dataset_audit_studio.api.review_schemas import (
    AestheticAuditListResponse,
    AIReviewDecisionRequest,
    AIReviewDecisionResponse,
    AIReviewListResponse,
    CuratedReviewDecisionRequest,
    CuratedReviewListResponse,
    DuplicateGroupAuditListResponse,
    SAEFeatureListResponse,
    StyleAuditListResponse,
    StyleReviewDecisionRequest,
    StyleReviewListResponse,
)
from dataset_audit_studio.database.enums import ReviewState
from dataset_audit_studio.reviews.service import ReviewService

router = APIRouter(prefix="/tasks/{task_id}/reviews", tags=["reviews"])


@router.get("/ai", response_model=AIReviewListResponse)
def list_ai_review_candidates(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    decision: ReviewState | None = None,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
) -> AIReviewListResponse:
    view = ReviewService(request.app.state.database).list_ai_candidates(
        task_id,
        offset=offset,
        limit=limit,
        decision=decision,
        folder=folder,
    )
    return AIReviewListResponse.from_view(view)


@router.post("/ai/decisions", response_model=AIReviewDecisionResponse)
def decide_ai_review_candidates(
    task_id: str,
    payload: AIReviewDecisionRequest,
    request: Request,
) -> AIReviewDecisionResponse:
    result = ReviewService(request.app.state.database).decide_ai_candidates(
        task_id,
        selection=payload.to_selection(),
        decision=payload.to_decision(),
    )
    return AIReviewDecisionResponse.from_result(result)


@router.get("/style", response_model=StyleReviewListResponse)
def list_style_review_candidates(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    decision: ReviewState | None = None,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
) -> StyleReviewListResponse:
    view = ReviewService(request.app.state.database).list_style_candidates(
        task_id,
        offset=offset,
        limit=limit,
        decision=decision,
        folder=folder,
    )
    return StyleReviewListResponse.from_view(view)


@router.get("/style/audit", response_model=StyleAuditListResponse)
def list_style_audit(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    decision: Literal["all", "pending_review", "approved_keep", "approved_exclude"] = "all",
) -> StyleAuditListResponse:
    view = ReviewService(request.app.state.database).list_style_audit(
        task_id,
        offset=offset,
        limit=limit,
        folder=folder,
        decision=decision,
    )
    return StyleAuditListResponse.from_view(view)


@router.get("/aesthetic/audit", response_model=AestheticAuditListResponse)
def list_aesthetic_audit(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    bucket: Annotated[float | None, Query(ge=1.0, le=5.0)] = None,
    reason_code: Literal[
        "missing",
        "non_finite",
        "out_of_range",
        "provenance_mismatch",
        "ambiguous",
    ] | None = None,
    decision: Literal["all", "pending_review", "approved_keep", "approved_exclude"] = "all",
) -> AestheticAuditListResponse:
    view = ReviewService(request.app.state.database).list_aesthetic_audit(
        task_id,
        offset=offset,
        limit=limit,
        folder=folder,
        bucket=bucket,
        reason_code=reason_code,
        decision=decision,
    )
    return AestheticAuditListResponse.from_view(view)


@router.post("/style/decisions", response_model=AIReviewDecisionResponse)
def decide_style_review_candidates(
    task_id: str,
    payload: StyleReviewDecisionRequest,
    request: Request,
) -> AIReviewDecisionResponse:
    result = ReviewService(request.app.state.database).decide_style_candidates(
        task_id,
        selection=payload.to_selection(),
        decision=payload.to_decision(),
    )
    return AIReviewDecisionResponse.from_result(result)


@router.post("/curated/decisions", response_model=AIReviewDecisionResponse)
def decide_curated_review_candidates(
    task_id: str,
    payload: CuratedReviewDecisionRequest,
    request: Request,
) -> AIReviewDecisionResponse:
    result = ReviewService(request.app.state.database).decide_curated_candidates(
        task_id,
        selection=payload.to_selection(),
        decision=payload.to_decision(),
    )
    return AIReviewDecisionResponse.from_result(result)


@router.get("/duplicates/audit", response_model=DuplicateGroupAuditListResponse)
def list_duplicate_group_audit(
    task_id: str,
    request: Request,
    evidence_type: Literal[
        "exact_duplicate",
        "visual_duplicate",
        "semantic_duplicate",
    ] = "exact_duplicate",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    decision: Literal["all", "pending_review", "approved_keep", "approved_exclude"] = "all",
) -> DuplicateGroupAuditListResponse:
    view = ReviewService(request.app.state.database).list_duplicate_group_audit(
        task_id,
        evidence_type=evidence_type,
        offset=offset,
        limit=limit,
        folder=folder,
        decision=decision,
    )
    return DuplicateGroupAuditListResponse.from_view(view)


@router.get("/curated", response_model=CuratedReviewListResponse)
def list_curated_review_candidates(
    task_id: str,
    request: Request,
    evidence_type: Literal[
        "aesthetic",
        "risk",
        "style_outlier",
        "duplicate",
        "exact_duplicate",
        "visual_duplicate",
        "semantic_duplicate",
    ] = "aesthetic",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    decision: ReviewState | None = None,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    reason_code: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    severity: Literal["info", "low", "medium", "high", "fatal"] | None = None,
    candidate_group: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    sample_id: Annotated[list[str] | None, Query(max_length=5000)] = None,
) -> CuratedReviewListResponse:
    view = ReviewService(request.app.state.database).list_curated_candidates(
        task_id,
        evidence_type=evidence_type,
        offset=offset,
        limit=limit,
        decision=decision,
        folder=folder,
        reason_code=reason_code,
        severity=severity,
        candidate_group=candidate_group,
        sample_ids=tuple(sample_id or ()),
    )
    return CuratedReviewListResponse.from_view(view)


@router.get("/sae/features", response_model=SAEFeatureListResponse)
def list_sae_features(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
) -> SAEFeatureListResponse:
    view = ReviewService(request.app.state.database).list_sae_features(
        task_id,
        offset=offset,
        limit=limit,
        folder=folder,
    )
    return SAEFeatureListResponse.from_view(view)
