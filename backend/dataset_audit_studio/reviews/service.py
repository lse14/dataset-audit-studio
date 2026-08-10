from __future__ import annotations

import math
from collections import defaultdict

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from dataset_audit_studio.adapters.dataset_workspace import DatasetWorkspaceRepository
from dataset_audit_studio.database.enums import ReviewState, TaskStatus
from dataset_audit_studio.database.models import (
    Artifact,
    Evidence,
    PhaseCheckpoint,
    ReviewDecision,
    Sample,
)
from dataset_audit_studio.database.review_overlay import active_human_overlay_by_sample
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import TaskDomainError
from dataset_audit_studio.jobs.service import TaskService
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
from dataset_audit_studio.scoring.assets import (
    AI_EVIDENCE_SOURCES,
    EVIDENCE_SOURCES,
    PREPROCESSING_VERSIONS,
)
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.repository import AI_REVIEW_CATEGORY
from dataset_audit_studio.workspace.curated import (
    AestheticScoreRecord,
    compute_curated_members,
    resolve_aesthetic_score,
)

AI_EVIDENCE_CODE = "ai_generated_probability"
STYLE_EVIDENCE_CODE = "artist_style_score"
STYLE_EVIDENCE_SOURCE = "artist_style_v1"
STYLE_REVIEW_CATEGORY = "style_outlier"
DUPLICATE_AUDIT_CODES = {
    "exact_duplicate": "duplicate_exact",
    "visual_duplicate": "duplicate_visual",
    "semantic_duplicate": "duplicate_semantic",
}
DUPLICATE_AUDIT_RESOLUTIONS = (512, 768, 1024, 1216, 1536)
AESTHETIC_AUDIT_BUCKETS = tuple(index / 2 for index in range(2, 11))
AESTHETIC_AUDIT_REASONS = (
    "missing",
    "non_finite",
    "out_of_range",
    "provenance_mismatch",
    "ambiguous",
)


class InvalidReviewSelection(TaskDomainError):
    pass


class ReviewService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_ai_candidates(
        self,
        task_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        decision: ReviewState | None = None,
        folder: str | None = None,
    ) -> AIReviewListView:
        TaskService(self.database).get_task(task_id)
        active = aliased(ReviewDecision)
        joins = self._candidate_joins(active)
        filters = list(self._candidate_filters(task_id))
        if decision is not None:
            filters.append(self._decision_filter(active, decision))
        if folder is not None:
            filters.append(Sample.artist_scope == folder)
        with self.database.read_session() as session:
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(Sample)
                    .join(Evidence, joins[0])
                    .outerjoin(active, joins[1])
                    .where(*filters)
                )
                or 0
            )
            rows = session.execute(
                select(Sample, Evidence, active)
                .join(Evidence, joins[0])
                .outerjoin(active, joins[1])
                .where(*filters)
                .order_by(Evidence.value_number.desc(), Sample.relative_path, Sample.id)
                .offset(offset)
                .limit(limit)
            ).all()
            counts = self._decision_counts(session, task_id, active, joins, folder=folder)

        items = tuple(self._candidate_view(*row) for row in rows)
        return AIReviewListView(
            items=items,
            total=total,
            pending=counts[ReviewState.PENDING_REVIEW.value],
            approved_keep=counts[ReviewState.APPROVED_KEEP.value],
            approved_exclude=counts[ReviewState.APPROVED_EXCLUDE.value],
            offset=offset,
            limit=limit,
        )

    def decide_ai_candidates(
        self,
        task_id: str,
        *,
        selection: ReviewSelection,
        decision: ReviewState,
    ) -> ReviewDecisionResult:
        task = TaskService(self.database).get_task(task_id)
        self._require_curated_confirmation_window(task, action="Candidate decisions")
        self._require_human_overlay_decision(decision)
        self._validate_selection(selection)
        with self.database.write_session() as session:
            rows = session.execute(
                select(Sample.id, Sample.artist_scope, Evidence.value_number)
                .join(Evidence, Evidence.sample_id == Sample.id)
                .where(*self._selection_filters(task_id, selection))
                .order_by(Sample.id)
            ).all()
            if not rows:
                return ReviewDecisionResult(selected=0, changed=0, decision=decision.value)
            sample_ids = [row.id for row in rows]
            active_by_sample = active_human_overlay_by_sample(
                session,
                task_id=task_id,
                sample_ids=sample_ids,
            )
            changed = 0
            selector_context = {
                "sample_ids": list(selection.sample_ids),
                "artist_scope": selection.artist_scope,
                "score_min": selection.score_min,
                "score_max": selection.score_max,
                "all_candidates": selection.all_candidates,
            }
            for row in rows:
                active = active_by_sample.get(row.id, ())
                previous = active[0] if active else None
                if previous is not None and previous.decision == decision.value:
                    continue
                for existing in active:
                    existing.is_active = False
                session.add(
                    ReviewDecision(
                        task_id=task_id,
                        sample_id=row.id,
                        scope_type="sample",
                        scope_id=row.id,
                        category=AI_REVIEW_CATEGORY,
                        decision=decision.value,
                        source="human",
                        context_json={
                            "selection": selector_context,
                            "probability": float(row.value_number or 0.0),
                        },
                        supersedes_id=previous.id if previous is not None else None,
                        is_active=True,
                    )
                )
                changed += 1
            return ReviewDecisionResult(
                selected=len(rows),
                changed=changed,
                decision=decision.value,
            )

    def list_style_candidates(
        self,
        task_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        decision: ReviewState | None = None,
        folder: str | None = None,
    ) -> StyleReviewListView:
        TaskService(self.database).get_task(task_id)
        active = aliased(ReviewDecision)
        joins = self._style_candidate_joins(active)
        filters = list(self._style_candidate_filters(task_id, active))
        if decision is not None:
            filters.append(self._decision_filter(active, decision))
        if folder is not None:
            filters.append(Sample.artist_scope == folder)
        with self.database.read_session() as session:
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(Sample)
                    .join(Evidence, joins[0])
                    .outerjoin(active, joins[1])
                    .where(*filters)
                )
                or 0
            )
            rows = session.execute(
                select(Sample, Evidence, active)
                .join(Evidence, joins[0])
                .outerjoin(active, joins[1])
                .where(*filters)
                .order_by(Evidence.value_number, Sample.relative_path, Sample.id)
                .offset(offset)
                .limit(limit)
            ).all()
            counts = self._style_decision_counts(session, task_id, active, joins, folder=folder)
        return StyleReviewListView(
            items=tuple(self._style_candidate_view(*row) for row in rows),
            total=total,
            pending=counts[ReviewState.PENDING_REVIEW.value],
            approved_keep=counts[ReviewState.APPROVED_KEEP.value],
            approved_exclude=counts[ReviewState.APPROVED_EXCLUDE.value],
            offset=offset,
            limit=limit,
        )

    def list_style_audit(
        self,
        task_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        folder: str | None = None,
        decision: str = "all",
    ) -> StyleAuditListView:
        TaskService(self.database).get_task(task_id)
        self._validate_audit_decision(decision)
        active = aliased(ReviewDecision)
        joins = self._style_candidate_joins(active)
        filters = list(self._style_audit_filters(task_id))
        if folder is not None:
            filters.append(Sample.artist_scope == folder)
        item_filters = list(filters)
        if decision != "all":
            item_filters.extend(
                (
                    or_(Evidence.severity.in_(("medium", "high")), active.source == "human"),
                    self._decision_filter(active, ReviewState(decision)),
                )
            )
        classification_order = case(
            (Evidence.severity == "high", 0),
            (Evidence.severity == "medium", 1),
            else_=2,
        )
        with self.database.read_session() as session:
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(Sample)
                    .join(Evidence, joins[0])
                    .outerjoin(active, joins[1])
                    .where(*item_filters)
                )
                or 0
            )
            classification_rows = session.execute(
                select(Evidence.severity, func.count())
                .select_from(Sample)
                .join(Evidence, joins[0])
                .where(*filters)
                .group_by(Evidence.severity)
            ).all()
            decision_filters = [
                *filters,
                or_(Evidence.severity.in_(("medium", "high")), active.source == "human"),
            ]
            decision_rows = session.execute(
                select(active.decision, func.count())
                .select_from(Sample)
                .join(Evidence, joins[0])
                .outerjoin(active, joins[1])
                .where(*decision_filters)
                .group_by(active.decision)
            ).all()
            rows = session.execute(
                select(Sample, Evidence, active)
                .join(Evidence, joins[0])
                .outerjoin(active, joins[1])
                .where(*item_filters)
                .order_by(
                    classification_order,
                    Evidence.value_number,
                    Sample.relative_path,
                    Sample.id,
                )
                .offset(offset)
                .limit(limit)
            ).all()

        class_counts = {"normal": 0, "outlier": 0, "strong_outlier": 0}
        for severity, count in classification_rows:
            class_counts[self._style_audit_classification(severity)] += int(count)
        decision_counts = {state.value: 0 for state in ReviewState}
        for decision, count in decision_rows:
            decision_counts[str(decision or ReviewState.PENDING_REVIEW.value)] += int(count)
        return StyleAuditListView(
            items=tuple(self._style_audit_view(*row) for row in rows),
            total=total,
            normal=class_counts["normal"],
            outlier=class_counts["outlier"],
            strong_outlier=class_counts["strong_outlier"],
            pending=decision_counts[ReviewState.PENDING_REVIEW.value],
            approved_keep=decision_counts[ReviewState.APPROVED_KEEP.value],
            approved_exclude=decision_counts[ReviewState.APPROVED_EXCLUDE.value],
            offset=offset,
            limit=limit,
        )

    def list_aesthetic_audit(
        self,
        task_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        folder: str | None = None,
        bucket: float | None = None,
        reason_code: str | None = None,
        decision: str = "all",
    ) -> AestheticAuditListView:
        task = TaskService(self.database).get_task(task_id)
        self._validate_audit_decision(decision)
        if bucket is not None and bucket not in AESTHETIC_AUDIT_BUCKETS:
            raise InvalidReviewSelection("Aesthetic audit buckets use 0.5 steps from 1.0 to 5.0")
        if reason_code is not None and reason_code not in AESTHETIC_AUDIT_REASONS:
            raise InvalidReviewSelection(f"Unsupported aesthetic audit reason: {reason_code}")

        with self.database.read_session() as session:
            sample_filters = [Sample.task_id == task.id, Sample.scan_state == "valid"]
            if folder is not None:
                sample_filters.append(Sample.artist_scope == folder)
            samples = session.scalars(
                select(Sample)
                .where(*sample_filters)
                .order_by(Sample.relative_path, Sample.id)
            ).all()
            sample_ids = tuple(sample.id for sample in samples)
            evidence_by_sample = self._aesthetic_score_records(
                session,
                task_id=task.id,
                sample_ids=sample_ids,
            )
            automatic_candidates = self._aesthetic_automatic_candidates(
                session,
                task,
                evidence_by_sample=evidence_by_sample,
            )
            active_by_sample = active_human_overlay_by_sample(
                session,
                task_id=task.id,
                sample_ids=sample_ids,
            )

        identity = self._aesthetic_identity(task)
        bucket_counts = {f"{value:.1f}": 0 for value in AESTHETIC_AUDIT_BUCKETS}
        invalid_counts = {reason: 0 for reason in AESTHETIC_AUDIT_REASONS}
        decision_counts = {state.value: 0 for state in ReviewState}
        items: list[AestheticAuditCandidateView] = []
        for sample in samples:
            score, invalid_reason = resolve_aesthetic_score(
                evidence_by_sample.get(sample.id, ()),
                identity=identity,
            )
            score_bucket = None
            if invalid_reason is None:
                assert score is not None
                score_bucket = self._aesthetic_bucket(score)
                bucket_counts[f"{score_bucket:.1f}"] += 1
            else:
                invalid_counts[invalid_reason] += 1

            active = active_by_sample.get(sample.id, ())
            active_decision = active[0] if active else None
            review_eligible = sample.id in automatic_candidates or active_decision is not None
            if review_eligible:
                decision_counts[
                    active_decision.decision
                    if active_decision is not None
                    else ReviewState.PENDING_REVIEW.value
                ] += 1
            items.append(
                AestheticAuditCandidateView(
                    sample_id=sample.id,
                    relative_path=sample.relative_path,
                    artist_scope=sample.artist_scope,
                    score=score,
                    bucket=score_bucket,
                    reason_code=invalid_reason,
                    review_eligible=review_eligible,
                    decision=(
                        active_decision.decision if active_decision is not None else None
                    ),
                    decision_source=(
                        active_decision.source if active_decision is not None else "none"
                    ),
                )
            )

        filtered = [
            item
            for item in items
            if (bucket is None or item.bucket == bucket)
            and (reason_code is None or item.reason_code == reason_code)
            and self._matches_audit_decision(
                decision,
                review_eligible=item.review_eligible,
                active_decision=item.decision,
            )
        ]
        filtered.sort(key=self._aesthetic_audit_sort_key)
        return AestheticAuditListView(
            items=tuple(filtered[offset : offset + limit]),
            total=len(filtered),
            bucket_counts=bucket_counts,
            invalid_counts=invalid_counts,
            pending=decision_counts[ReviewState.PENDING_REVIEW.value],
            approved_keep=decision_counts[ReviewState.APPROVED_KEEP.value],
            approved_exclude=decision_counts[ReviewState.APPROVED_EXCLUDE.value],
            offset=offset,
            limit=limit,
        )

    def decide_style_candidates(
        self,
        task_id: str,
        *,
        selection: ReviewSelection,
        decision: ReviewState,
    ) -> ReviewDecisionResult:
        task = TaskService(self.database).get_task(task_id)
        self._require_curated_confirmation_window(task, action="Candidate decisions")
        self._require_human_overlay_decision(decision)
        self._validate_selection(selection)
        with self.database.write_session() as session:
            active_alias = aliased(ReviewDecision)
            filters = list(self._style_candidate_filters(task_id, active_alias))
            if selection.sample_ids:
                filters.append(Sample.id.in_(selection.sample_ids))
            if selection.artist_scope is not None:
                filters.append(Sample.artist_scope == selection.artist_scope)
            if selection.score_min is not None:
                filters.append(Evidence.value_number >= selection.score_min)
            if selection.score_max is not None:
                filters.append(Evidence.value_number <= selection.score_max)
            rows = session.execute(
                select(Sample.id, Evidence.value_number)
                .join(Evidence, Evidence.sample_id == Sample.id)
                .outerjoin(
                    active_alias,
                    and_(
                        active_alias.sample_id == Sample.id,
                        active_alias.task_id == Sample.task_id,
                        active_alias.category == STYLE_REVIEW_CATEGORY,
                        active_alias.is_active.is_(True),
                    ),
                )
                .where(*filters)
                .order_by(Sample.id)
            ).all()
            if not rows:
                return ReviewDecisionResult(0, 0, decision.value)
            sample_ids = [row.id for row in rows]
            active_by_sample = active_human_overlay_by_sample(
                session,
                task_id=task_id,
                sample_ids=sample_ids,
            )
            changed = 0
            selector = {
                "sample_ids": list(selection.sample_ids),
                "artist_scope": selection.artist_scope,
                "score_min": selection.score_min,
                "score_max": selection.score_max,
                "all_candidates": selection.all_candidates,
            }
            for row in rows:
                active = active_by_sample.get(row.id, ())
                previous = active[0] if active else None
                if previous is not None and previous.decision == decision.value:
                    continue
                for existing in active:
                    existing.is_active = False
                session.add(
                    ReviewDecision(
                        task_id=task_id,
                        sample_id=row.id,
                        scope_type="sample",
                        scope_id=row.id,
                        category=STYLE_REVIEW_CATEGORY,
                        decision=decision.value,
                        source="human",
                        context_json={
                            "selection": selector,
                            "style_score": float(row.value_number or 0.0),
                        },
                        supersedes_id=previous.id if previous is not None else None,
                        is_active=True,
                    )
                )
                changed += 1
            return ReviewDecisionResult(len(rows), changed, decision.value)

    def list_duplicate_group_audit(
        self,
        task_id: str,
        *,
        evidence_type: str,
        offset: int = 0,
        limit: int = 100,
        folder: str | None = None,
        decision: str = "all",
    ) -> DuplicateGroupAuditListView:
        task = TaskService(self.database).get_task(task_id)
        self._validate_audit_decision(decision)
        code = DUPLICATE_AUDIT_CODES.get(evidence_type)
        if code is None:
            raise InvalidReviewSelection(f"Unsupported duplicate evidence type: {evidence_type}")

        with self.database.read_session() as session:
            rows = session.execute(
                select(Sample, Evidence)
                .join(Evidence, Evidence.sample_id == Sample.id)
                .where(
                    Sample.task_id == task.id,
                    Evidence.task_id == task.id,
                    Evidence.code == code,
                )
                .order_by(Sample.relative_path, Sample.id, Evidence.id)
            ).all()
            unresolved = 0
            grouped: dict[str, dict[str, tuple[Sample, Evidence]]] = {}
            for sample, evidence in rows:
                metadata = (
                    evidence.metadata_json
                    if isinstance(evidence.metadata_json, dict)
                    else {}
                )
                group_key = metadata.get("group_key")
                if not isinstance(group_key, str) or not group_key.strip():
                    unresolved += 1
                    continue
                members = grouped.setdefault(group_key, {})
                existing = members.get(sample.id)
                if existing is None or (
                    self._duplicate_evidence_sort_key(evidence)
                    < self._duplicate_evidence_sort_key(existing[1])
                ):
                    members[sample.id] = (sample, evidence)

            resolved_groups = [
                (
                    group_key,
                    tuple(
                        sorted(
                            members.values(),
                            key=lambda row: (row[0].relative_path, row[0].id),
                        )
                    ),
                )
                for group_key, members in sorted(grouped.items())
                if folder is None
                or any(
                    sample.artist_scope == folder
                    for sample, _evidence in members.values()
                )
            ]
            sample_ids = tuple(
                dict.fromkeys(
                    sample.id
                    for _group_key, members in resolved_groups
                    for sample, _evidence in members
                )
            )
            active_by_sample = active_human_overlay_by_sample(
                session,
                task_id=task.id,
                sample_ids=sample_ids,
            )
            pixel_areas_by_sample: dict[str, int] = {}
            resolution_rows = []
            for sample_id in sample_ids:
                sample = next(
                    (
                        item
                        for _group, values in resolved_groups
                        for item, _evidence in values
                        if item.id == sample_id
                    ),
                    None,
                )
                if sample is None:
                    continue
                width = sample.display_width or sample.encoded_width
                height = sample.display_height or sample.encoded_height
                if not isinstance(width, int) or not isinstance(height, int):
                    continue
                pixel_area = width * height
                pixel_areas_by_sample[sample_id] = pixel_area
                resolution_rows.extend(
                    (sample_id, resolution)
                    for resolution in DUPLICATE_AUDIT_RESOLUTIONS
                    if pixel_area >= resolution * resolution
                )

        resolutions_by_sample: dict[str, list[int]] = {}
        for sample_id, resolution in resolution_rows:
            values = resolutions_by_sample.setdefault(sample_id, [])
            if resolution not in values:
                values.append(resolution)

        groups: list[DuplicateGroupAuditView] = []
        for group_key, members in resolved_groups:
            member_views: list[DuplicateGroupMemberAuditView] = []
            counts = {state.value: 0 for state in ReviewState}
            effective_retained_count = 0
            for sample, evidence in members:
                active = active_by_sample.get(sample.id, ())
                active_decision = active[0] if active else None
                decision_value = (
                    active_decision.decision if active_decision is not None else None
                )
                effective_decision = decision_value or ReviewState.PENDING_REVIEW.value
                counts[effective_decision] += 1
                if effective_decision != ReviewState.APPROVED_EXCLUDE.value:
                    effective_retained_count += 1
                member_views.append(
                    DuplicateGroupMemberAuditView(
                        sample_id=sample.id,
                        relative_path=sample.relative_path,
                        artist_scope=sample.artist_scope,
                        score=evidence.value_number,
                        decision=decision_value,
                        decision_source=(
                            active_decision.source if active_decision is not None else "automatic"
                        ),
                        review_eligible=True,
                        pixel_area=pixel_areas_by_sample.get(sample.id),
                        resolutions=tuple(resolutions_by_sample.get(sample.id, ())),
                    )
                )
            groups.append(
                DuplicateGroupAuditView(
                    group_key=group_key,
                    evidence_type=evidence_type,
                    member_count=len(member_views),
                    pending=counts[ReviewState.PENDING_REVIEW.value],
                    approved_keep=counts[ReviewState.APPROVED_KEEP.value],
                    approved_exclude=counts[ReviewState.APPROVED_EXCLUDE.value],
                    effective_retained_count=effective_retained_count,
                    members=tuple(member_views),
                )
            )

        if decision != "all":
            groups = [
                group
                for group in groups
                if self._matches_duplicate_group_decision(group, decision)
            ]

        totals = {state.value: 0 for state in ReviewState}
        for group in groups:
            totals[ReviewState.PENDING_REVIEW.value] += group.pending
            totals[ReviewState.APPROVED_KEEP.value] += group.approved_keep
            totals[ReviewState.APPROVED_EXCLUDE.value] += group.approved_exclude
        return DuplicateGroupAuditListView(
            items=tuple(groups[offset : offset + limit]),
            total=len(groups),
            pending=totals[ReviewState.PENDING_REVIEW.value],
            approved_keep=totals[ReviewState.APPROVED_KEEP.value],
            approved_exclude=totals[ReviewState.APPROVED_EXCLUDE.value],
            unresolved=unresolved,
            offset=offset,
            limit=limit,
        )

    def list_curated_candidates(
        self,
        task_id: str,
        *,
        evidence_type: str,
        offset: int = 0,
        limit: int = 100,
        decision: ReviewState | None = None,
        folder: str | None = None,
        reason_code: str | None = None,
        sample_ids: tuple[str, ...] = (),
        severity: str | None = None,
        candidate_group: str | None = None,
    ) -> CuratedReviewListView:
        task = TaskService(self.database).get_task(task_id)
        if not (
            task.status == TaskStatus.EVIDENCE_REVIEW.value
            or (
                task.status == TaskStatus.PAUSED.value
                and task.resume_state == TaskStatus.EVIDENCE_REVIEW.value
            )
        ):
            raise InvalidReviewSelection(
                "Curated candidates require the curated confirmation window"
            )
        with self.database.read_session() as session:
            candidates = self._curated_candidate_views(
                session,
                task,
                evidence_type=evidence_type,
                folder=folder,
                reason_code=reason_code,
                sample_ids=sample_ids,
                severity=severity,
                candidate_group=candidate_group,
            )
        candidates.sort(key=lambda item: (item.relative_path, item.sample_id))
        counts = {state.value: 0 for state in ReviewState}
        for candidate in candidates:
            counts[candidate.decision] = counts.get(candidate.decision, 0) + 1
        if decision is not None:
            candidates = [
                candidate for candidate in candidates if candidate.decision == decision.value
            ]
        total = len(candidates)
        return CuratedReviewListView(
            items=tuple(candidates[offset : offset + limit]),
            total=total,
            pending=counts[ReviewState.PENDING_REVIEW.value],
            approved_keep=counts[ReviewState.APPROVED_KEEP.value],
            approved_exclude=counts[ReviewState.APPROVED_EXCLUDE.value],
            offset=offset,
            limit=limit,
        )

    def _curated_candidate_views(
        self,
        session: Session,
        task,
        *,
        evidence_type: str,
        folder: str | None,
        reason_code: str | None,
        sample_ids: tuple[str, ...],
        severity: str | None,
        candidate_group: str | None,
    ) -> list[CuratedReviewCandidateView]:
        candidates: list[tuple[Sample, str, float | None, str | None, str | None]] = []
        if evidence_type == "aesthetic":
            memberships = DatasetWorkspaceRepository().curated_memberships(
                session,
                task,
                include_human_decisions=False,
            )
            automatic = {
                item.sample_id: item for item in memberships if item.reason_code is not None
            }
            rows = session.scalars(
                select(Sample).where(
                    Sample.task_id == task.id,
                    Sample.id.in_(tuple(automatic)),
                )
            ).all()
            for sample in rows:
                membership = automatic[sample.id]
                if sample_ids and sample.id not in sample_ids:
                    continue
                if folder is not None and sample.artist_scope != folder:
                    continue
                if reason_code is not None and membership.reason_code != reason_code:
                    continue
                if severity is not None or candidate_group is not None:
                    continue
                candidates.append(
                    (
                        sample,
                        membership.reason_code or "",
                        membership.score,
                        None,
                        None,
                    )
                )
        else:
            rows = session.execute(
                select(Sample, Evidence)
                .join(Evidence, Evidence.sample_id == Sample.id)
                .where(Sample.task_id == task.id, Evidence.task_id == task.id)
                .order_by(Sample.id, Evidence.id)
            ).all()
            unique: dict[str, tuple[Sample, str, float | None, str | None, str | None]] = {}
            for sample, evidence in rows:
                if not self._is_curated_evidence(evidence, evidence_type):
                    continue
                if sample_ids and sample.id not in sample_ids:
                    continue
                if folder is not None and sample.artist_scope != folder:
                    continue
                if reason_code is not None and evidence.code != reason_code:
                    continue
                if severity is not None and evidence.severity != severity:
                    continue
                metadata = (
                    evidence.metadata_json if isinstance(evidence.metadata_json, dict) else {}
                )
                group_key = metadata.get("group_key")
                if candidate_group is not None and group_key != candidate_group:
                    continue
                unique.setdefault(
                    sample.id,
                    (
                        sample,
                        evidence.code,
                        evidence.value_number,
                        evidence.severity,
                        group_key if isinstance(group_key, str) else None,
                    ),
                )
            candidates.extend(unique.values())

        active_by_sample = active_human_overlay_by_sample(
            session,
            task_id=task.id,
            sample_ids=(candidate[0].id for candidate in candidates),
        )
        return [
            CuratedReviewCandidateView(
                sample_id=sample.id,
                relative_path=sample.relative_path,
                artist_scope=sample.artist_scope,
                evidence_type=evidence_type,
                reason_code=candidate_reason,
                score=score,
                severity=candidate_severity,
                candidate_group=group_key,
                decision=(
                    active[0].decision
                    if (active := active_by_sample.get(sample.id, ()))
                    else ReviewState.PENDING_REVIEW.value
                ),
                decision_source=active[0].source if active else "automatic",
                decision_id=active[0].id if active else None,
                decision_created_at=active[0].created_at if active else None,
            )
            for sample, candidate_reason, score, candidate_severity, group_key in candidates
        ]

    def decide_curated_candidates(
        self,
        task_id: str,
        *,
        selection: CuratedReviewSelection,
        decision: ReviewState,
    ) -> ReviewDecisionResult:
        task = TaskService(self.database).get_task(task_id)
        self._require_curated_confirmation_window(task, action="Curated decisions")
        self._require_human_overlay_decision(decision)
        if not selection.evidence_type.strip():
            raise InvalidReviewSelection("Curated evidence type is required")
        if not (
            selection.sample_ids
            or selection.artist_scope is not None
            or selection.severity is not None
            or selection.candidate_group is not None
        ):
            raise InvalidReviewSelection("An explicit curated review selector is required")
        if len(selection.sample_ids) > 5000:
            raise InvalidReviewSelection("Curated decisions allow at most 5000 samples")
        category = f"curated:{selection.evidence_type}"
        with self.database.write_session() as session:
            candidates: list[tuple[Sample, dict[str, object]]] = []
            if selection.evidence_type == "aesthetic":
                rows = session.scalars(
                    select(Sample)
                    .where(Sample.task_id == task_id, Sample.scan_state == "valid")
                    .order_by(Sample.id)
                ).all()
                evidence_by_sample = self._aesthetic_score_records(
                    session,
                    task_id=task_id,
                    sample_ids=tuple(sample.id for sample in rows),
                )
                automatic = self._aesthetic_automatic_candidates(
                    session,
                    task,
                    evidence_by_sample=evidence_by_sample,
                )
                active_by_sample = active_human_overlay_by_sample(
                    session,
                    task_id=task_id,
                    sample_ids=(sample.id for sample in rows),
                )
                for sample in rows:
                    active = active_by_sample.get(sample.id, ())
                    membership = automatic.get(sample.id)
                    if membership is None and not active:
                        continue
                    if selection.sample_ids and sample.id not in selection.sample_ids:
                        continue
                    if (
                        selection.artist_scope is not None
                        and sample.artist_scope != selection.artist_scope
                    ):
                        continue
                    if selection.severity is not None or selection.candidate_group is not None:
                        continue
                    candidates.append(
                        (
                            sample,
                            {
                                "reason_code": membership.reason_code if membership else None,
                                "score": membership.score if membership else None,
                                "active_overlay": bool(active),
                            },
                        )
                    )
            else:
                rows = session.execute(
                    select(Sample, Evidence)
                    .join(Evidence, Evidence.sample_id == Sample.id)
                    .where(Sample.task_id == task_id, Evidence.task_id == task_id)
                    .order_by(Sample.id, Evidence.id)
                ).all()
                for sample, evidence in rows:
                    if selection.sample_ids and sample.id not in selection.sample_ids:
                        continue
                    if (
                        selection.artist_scope is not None
                        and sample.artist_scope != selection.artist_scope
                    ):
                        continue
                    if (
                        selection.severity is not None
                        and evidence.severity != selection.severity
                    ):
                        continue
                    metadata = (
                        evidence.metadata_json
                        if isinstance(evidence.metadata_json, dict)
                        else {}
                    )
                    group_key = metadata.get("group_key")
                    if (
                        selection.candidate_group is not None
                        and group_key != selection.candidate_group
                    ):
                        continue
                    if not self._is_curated_evidence(evidence, selection.evidence_type):
                        continue
                    candidates.append((sample, dict(metadata)))
            unique: dict[str, tuple[Sample, dict[str, object]]] = {}
            for sample, metadata in candidates:
                unique.setdefault(sample.id, (sample, metadata))
            if len(unique) > 5000:
                raise InvalidReviewSelection("Curated decisions allow at most 5000 samples")
            if not unique:
                return ReviewDecisionResult(0, 0, decision.value)
            sample_ids = tuple(unique)
            active_by_sample = active_human_overlay_by_sample(
                session,
                task_id=task_id,
                sample_ids=sample_ids,
            )
            changed = 0
            selector = {
                "evidence_type": selection.evidence_type,
                "sample_ids": list(selection.sample_ids),
                "artist_scope": selection.artist_scope,
                "severity": selection.severity,
                "candidate_group": selection.candidate_group,
            }
            for sample_id, (_sample, metadata) in unique.items():
                active = active_by_sample.get(sample_id, ())
                previous = active[0] if active else None
                if previous is not None and previous.decision == decision.value:
                    continue
                for existing in active:
                    existing.is_active = False
                session.add(
                    ReviewDecision(
                        task_id=task_id,
                        sample_id=sample_id,
                        scope_type="sample",
                        scope_id=sample_id,
                        category=category,
                        decision=decision.value,
                        source="human",
                        context_json={"selection": selector, "evidence": metadata},
                        supersedes_id=previous.id if previous is not None else None,
                        is_active=True,
                    )
                )
                changed += 1
            return ReviewDecisionResult(len(unique), changed, decision.value)

    @staticmethod
    def _is_curated_evidence(evidence: Evidence, evidence_type: str) -> bool:
        code = evidence.code
        if evidence_type == "risk":
            return evidence.severity in {"fatal", "high", "medium", "low"}
        if evidence_type == "style_outlier":
            return code == STYLE_EVIDENCE_CODE and evidence.source == STYLE_EVIDENCE_SOURCE
        if evidence_type in {
            "duplicate",
            "exact_duplicate",
            "visual_duplicate",
            "semantic_duplicate",
        }:
            if code == "cross_artist_duplicate":
                return evidence_type in {"duplicate", "visual_duplicate", "exact_duplicate"}
            if evidence_type == "duplicate":
                return code in {
                    "duplicate_exact",
                    "duplicate_visual",
                    "duplicate_semantic",
                }
            return code == {
                "exact_duplicate": "duplicate_exact",
                "visual_duplicate": "duplicate_visual",
                "semantic_duplicate": "duplicate_semantic",
            }[evidence_type]
        raise InvalidReviewSelection(f"Unsupported curated evidence type: {evidence_type}")

    @staticmethod
    def _duplicate_evidence_sort_key(evidence: Evidence) -> tuple[float, str]:
        score = evidence.value_number
        return (-(float(score) if score is not None else float("-inf")), evidence.id)

    def list_sae_features(
        self,
        task_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        folder: str | None = None,
    ) -> SAEFeatureListView:
        TaskService(self.database).get_task(task_id)
        with self.database.read_session() as session:
            artifact = self._current_sae_artifact(session, task_id)
            metadata = dict(artifact.metadata_json)
            thresholds = list(metadata.get("thresholds", []))
            sample_ids = list(metadata.get("sample_ids", []))
            top_indices = list(metadata.get("top_indices", []))
            if len(thresholds) != len(top_indices):
                raise RuntimeError("Current SAE artifact metadata is inconsistent")
            end = min(offset + limit, len(thresholds))
            top_sample_ids_by_feature = {
                feature_id: tuple(
                    sample_ids[int(index)]
                    for index in top_indices[feature_id]
                    if 0 <= int(index) < len(sample_ids)
                )
                for feature_id in range(offset, end)
            }
            visible_sample_ids = {
                sample_id
                for feature_sample_ids in top_sample_ids_by_feature.values()
                for sample_id in feature_sample_ids
            }
            representative_by_id: dict[str, SAERepresentativeSampleView] = {}
            if visible_sample_ids:
                sample_filters = [
                    Sample.task_id == task_id,
                    Sample.id.in_(visible_sample_ids),
                ]
                if folder is not None:
                    sample_filters.append(Sample.artist_scope == folder)
                representative_by_id = {
                    sample_id: SAERepresentativeSampleView(
                        sample_id=sample_id,
                        relative_path=relative_path,
                    )
                    for sample_id, relative_path in session.execute(
                        select(Sample.id, Sample.relative_path).where(*sample_filters)
                    )
                }
            items = tuple(
                SAEFeatureView(
                    feature_id=feature_id,
                    threshold=float(thresholds[feature_id]),
                    top_sample_ids=top_sample_ids_by_feature[feature_id],
                    representative_samples=tuple(
                        representative_by_id[sample_id]
                        for sample_id in top_sample_ids_by_feature[feature_id]
                        if sample_id in representative_by_id
                    )[:3],
                )
                for feature_id in range(offset, end)
            )
        return SAEFeatureListView(
            cache_key=artifact.cache_key,
            items=items,
            total=len(thresholds),
            offset=offset,
            limit=limit,
        )

    @staticmethod
    def _aesthetic_identity(task) -> dict[str, str]:
        scoring = ScoringConfig.from_task_config(task.config)
        return {
            "source": EVIDENCE_SOURCES["aesthetic"],
            "model_id": scoring.aesthetic.model_id,
            "config_hash": scoring.inference_config_hash("aesthetic"),
            "algorithm_version": PREPROCESSING_VERSIONS["aesthetic"],
        }

    @staticmethod
    def _aesthetic_minimum(task) -> float | None:
        components = task.config.get("components")
        if not isinstance(components, dict):
            return None
        export = components.get("export.dataset")
        config = export.get("config") if isinstance(export, dict) else None
        minimum = config.get("aesthetic_minimum") if isinstance(config, dict) else None
        if minimum is None:
            return None
        if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
            raise InvalidReviewSelection("Aesthetic minimum must be numeric")
        value = float(minimum)
        if not math.isfinite(value) or not 1.0 <= value <= 5.0:
            raise InvalidReviewSelection("Aesthetic minimum must be finite and between 1 and 5")
        if not ScoringConfig.from_task_config(task.config).aesthetic.enabled:
            raise InvalidReviewSelection("Aesthetic curation requires enabled aesthetic scoring")
        return value

    @staticmethod
    def _aesthetic_score_records(
        session: Session,
        *,
        task_id: str,
        sample_ids: tuple[str, ...],
    ) -> dict[str, list[AestheticScoreRecord]]:
        if not sample_ids:
            return {}
        rows = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task_id,
                Evidence.sample_id.in_(sample_ids),
                Evidence.code == "aesthetic_score",
            )
            .order_by(Evidence.sample_id, Evidence.id)
        ).all()
        records: dict[str, list[AestheticScoreRecord]] = defaultdict(list)
        for row in rows:
            metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            value = row.value_json
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                value = row.value_number
            records[row.sample_id].append(
                AestheticScoreRecord(
                    value=value,
                    source=row.source,
                    model_id=metadata.get("model_id"),
                    config_hash=metadata.get("config_hash"),
                    algorithm_version=row.algorithm_version,
                )
            )
        return records

    def _aesthetic_automatic_candidates(
        self,
        session: Session,
        task,
        *,
        evidence_by_sample: dict[str, list[AestheticScoreRecord]],
    ) -> dict[str, object]:
        minimum = self._aesthetic_minimum(task)
        if minimum is None:
            return {}
        broad_ids = tuple(
            session.scalars(
                select(Sample.id)
                .where(Sample.task_id == task.id, Sample.scan_state == "valid")
                .order_by(Sample.id)
            ).all()
        )
        memberships = compute_curated_members(
            broad_ids,
            aesthetic_evidence=evidence_by_sample,
            aesthetic_minimum=minimum,
            aesthetic_identity=self._aesthetic_identity(task),
            human_decisions=None,
        )
        return {
            membership.sample_id: membership
            for membership in memberships
            if membership.reason_code is not None
        }

    @staticmethod
    def _aesthetic_bucket(score: float) -> float:
        return min(5.0, math.floor(score * 2) / 2)

    @staticmethod
    def _aesthetic_audit_sort_key(item: AestheticAuditCandidateView):
        if item.bucket is not None:
            return (0, item.bucket, item.score or 0.0, item.relative_path, item.sample_id)
        return (
            1,
            AESTHETIC_AUDIT_REASONS.index(item.reason_code or "missing"),
            0.0,
            item.relative_path,
            item.sample_id,
        )

    @staticmethod
    def _candidate_joins(active):
        return (
            Evidence.sample_id == Sample.id,
            and_(
                active.sample_id == Sample.id,
                active.task_id == Sample.task_id,
                active.category == AI_REVIEW_CATEGORY,
                active.is_active.is_(True),
            ),
        )

    @staticmethod
    def _style_candidate_joins(active):
        return (
            Evidence.sample_id == Sample.id,
            and_(
                active.sample_id == Sample.id,
                active.task_id == Sample.task_id,
                active.category == STYLE_REVIEW_CATEGORY,
                active.is_active.is_(True),
            ),
        )

    @staticmethod
    def _style_candidate_filters(task_id: str, active):
        return (
            Sample.task_id == task_id,
            Evidence.task_id == task_id,
            Evidence.code == STYLE_EVIDENCE_CODE,
            Evidence.source == STYLE_EVIDENCE_SOURCE,
            Evidence.value_number.is_not(None),
            or_(Evidence.severity.in_(("medium", "high")), active.source == "human"),
        )

    @staticmethod
    def _style_audit_filters(task_id: str):
        return (
            Sample.task_id == task_id,
            Evidence.task_id == task_id,
            Evidence.code == STYLE_EVIDENCE_CODE,
            Evidence.source == STYLE_EVIDENCE_SOURCE,
            Evidence.value_number.is_not(None),
            Evidence.threshold_number.is_not(None),
        )

    @staticmethod
    def _style_audit_classification(severity: str | None) -> str:
        if severity == "high":
            return "strong_outlier"
        if severity == "medium":
            return "outlier"
        return "normal"

    @staticmethod
    def _candidate_filters(task_id: str):
        return (
            Sample.task_id == task_id,
            Evidence.task_id == task_id,
            Evidence.code == AI_EVIDENCE_CODE,
            Evidence.source.in_(tuple(AI_EVIDENCE_SOURCES.values())),
            Evidence.value_number.is_not(None),
            Evidence.threshold_number.is_not(None),
            Evidence.value_number >= Evidence.threshold_number,
        )

    def _selection_filters(self, task_id: str, selection: ReviewSelection):
        filters = list(self._candidate_filters(task_id))
        if selection.sample_ids:
            filters.append(Sample.id.in_(selection.sample_ids))
        if selection.artist_scope is not None:
            filters.append(Sample.artist_scope == selection.artist_scope)
        if selection.score_min is not None:
            filters.append(Evidence.value_number >= selection.score_min)
        if selection.score_max is not None:
            filters.append(Evidence.value_number <= selection.score_max)
        return filters

    @staticmethod
    def _validate_selection(selection: ReviewSelection) -> None:
        if not (
            selection.sample_ids
            or selection.artist_scope is not None
            or selection.score_min is not None
            or selection.score_max is not None
            or selection.all_candidates
        ):
            raise InvalidReviewSelection(
                "AI review decision requires an explicit sample, artist, score range, or all flag"
            )
        if (
            selection.score_min is not None
            and selection.score_max is not None
            and selection.score_min > selection.score_max
        ):
            raise InvalidReviewSelection("score_min cannot exceed score_max")

    @staticmethod
    def _require_curated_confirmation_window(task, *, action: str) -> None:
        if task.status == TaskStatus.COMPLETED.value:
            return
        if task.status == TaskStatus.EVIDENCE_REVIEW.value:
            return
        if (
            task.status == TaskStatus.PAUSED.value
            and task.resume_state == TaskStatus.EVIDENCE_REVIEW.value
        ):
            return
        raise InvalidReviewSelection(f"{action} require the curated confirmation window")

    @staticmethod
    def _require_human_overlay_decision(decision: ReviewState) -> None:
        if decision in {ReviewState.APPROVED_KEEP, ReviewState.APPROVED_EXCLUDE}:
            return
        raise InvalidReviewSelection("Review decisions must approve keep or exclude")

    @staticmethod
    def _decision_filter(active, decision: ReviewState):
        if decision is ReviewState.PENDING_REVIEW:
            return or_(
                active.id.is_(None),
                active.decision == ReviewState.PENDING_REVIEW.value,
            )
        return active.decision == decision.value

    @staticmethod
    def _validate_audit_decision(decision: str) -> None:
        if decision == "all" or decision in {state.value for state in ReviewState}:
            return
        raise InvalidReviewSelection(f"Unsupported audit decision: {decision}")

    @staticmethod
    def _matches_audit_decision(
        requested: str,
        *,
        review_eligible: bool,
        active_decision: str | None,
    ) -> bool:
        if requested == "all":
            return True
        if requested == ReviewState.PENDING_REVIEW.value:
            return review_eligible and active_decision is None
        return active_decision == requested

    @staticmethod
    def _matches_duplicate_group_decision(
        group: DuplicateGroupAuditView,
        requested: str,
    ) -> bool:
        return {
            ReviewState.PENDING_REVIEW.value: group.pending,
            ReviewState.APPROVED_KEEP.value: group.approved_keep,
            ReviewState.APPROVED_EXCLUDE.value: group.approved_exclude,
        }[requested] > 0

    def _decision_counts(
        self,
        session: Session,
        task_id: str,
        active,
        joins,
        *,
        folder: str | None,
    ) -> dict[str, int]:
        filters = list(self._candidate_filters(task_id))
        if folder is not None:
            filters.append(Sample.artist_scope == folder)
        rows = session.execute(
            select(active.decision, func.count())
            .select_from(Sample)
            .join(Evidence, joins[0])
            .outerjoin(active, joins[1])
            .where(*filters)
            .group_by(active.decision)
        ).all()
        counts = {state.value: 0 for state in ReviewState}
        for decision, count in rows:
            counts[str(decision or ReviewState.PENDING_REVIEW.value)] += int(count)
        return counts

    def _style_decision_counts(
        self,
        session,
        task_id: str,
        active,
        joins,
        *,
        folder: str | None,
    ) -> dict[str, int]:
        filters = list(self._style_candidate_filters(task_id, active))
        if folder is not None:
            filters.append(Sample.artist_scope == folder)
        rows = session.execute(
            select(active.decision, func.count())
            .select_from(Sample)
            .join(Evidence, joins[0])
            .outerjoin(active, joins[1])
            .where(*filters)
            .group_by(active.decision)
        ).all()
        counts = {state.value: 0 for state in ReviewState}
        for decision, count in rows:
            counts[str(decision or ReviewState.PENDING_REVIEW.value)] += int(count)
        return counts

    @staticmethod
    def _candidate_view(
        sample: Sample,
        evidence: Evidence,
        decision: ReviewDecision | None,
    ) -> AIReviewCandidateView:
        metadata = dict(evidence.metadata_json or {})
        return AIReviewCandidateView(
            sample_id=sample.id,
            relative_path=sample.relative_path,
            artist_scope=sample.artist_scope,
            probability=float(evidence.value_number or 0.0),
            threshold=float(evidence.threshold_number or 0.0),
            reference_threshold=float(metadata.get("reference_threshold", 0.5)),
            decision=(
                decision.decision
                if decision is not None
                else ReviewState.PENDING_REVIEW.value
            ),
            decision_source=decision.source if decision is not None else "model",
            decision_id=decision.id if decision is not None else None,
            decision_created_at=decision.created_at if decision is not None else None,
        )

    @staticmethod
    def _style_candidate_view(
        sample: Sample,
        evidence: Evidence,
        decision: ReviewDecision | None,
    ) -> StyleReviewCandidateView:
        metadata = dict(evidence.metadata_json or {})
        return StyleReviewCandidateView(
            sample_id=sample.id,
            relative_path=sample.relative_path,
            artist_scope=sample.artist_scope,
            style_score=float(evidence.value_number or 0.0),
            threshold=float(evidence.threshold_number or 0.0),
            strong_outlier=bool(metadata.get("strong_outlier", False)),
            reason=metadata.get("outlier_reason"),
            decision=(
                decision.decision
                if decision is not None
                else ReviewState.PENDING_REVIEW.value
            ),
            decision_source=decision.source if decision is not None else "model",
            decision_id=decision.id if decision is not None else None,
            decision_created_at=decision.created_at if decision is not None else None,
        )

    @classmethod
    def _style_audit_view(
        cls,
        sample: Sample,
        evidence: Evidence,
        decision: ReviewDecision | None,
    ) -> StyleAuditCandidateView:
        classification = cls._style_audit_classification(evidence.severity)
        candidate = classification != "normal" or (
            decision is not None and decision.source == "human"
        )
        metadata = dict(evidence.metadata_json or {})
        return StyleAuditCandidateView(
            sample_id=sample.id,
            relative_path=sample.relative_path,
            artist_scope=sample.artist_scope,
            style_score=float(evidence.value_number or 0.0),
            threshold=float(evidence.threshold_number or 0.0),
            classification=classification,
            reason=metadata.get("outlier_reason"),
            review_eligible=candidate,
            decision=(
                decision.decision
                if decision is not None
                else ReviewState.PENDING_REVIEW.value
                if candidate
                else None
            ),
            decision_source=(
                decision.source
                if decision is not None
                else "model"
                if candidate
                else "none"
            ),
        )

    @staticmethod
    def _current_sae_artifact(session: Session, task_id: str) -> Artifact:
        checkpoint = session.scalar(
            select(PhaseCheckpoint)
            .where(
                PhaseCheckpoint.task_id == task_id,
                PhaseCheckpoint.phase == "semantic_clustering",
            )
            .order_by(PhaseCheckpoint.created_at.desc(), PhaseCheckpoint.id.desc())
            .limit(1)
        )
        cache_key = checkpoint.cursor_json.get("sae_cache_key") if checkpoint else None
        if not cache_key:
            raise InvalidReviewSelection("Task has no current SAE artifact")
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.task_id == task_id,
                Artifact.kind == "siglip_sae",
                Artifact.cache_key == cache_key,
                Artifact.state == "ready",
            )
        )
        if artifact is None:
            raise InvalidReviewSelection("Current SAE artifact is not ready")
        return artifact
