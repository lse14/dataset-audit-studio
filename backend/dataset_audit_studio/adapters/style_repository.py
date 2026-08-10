from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dataset_audit_studio.components.artist_style.assets import (
    STYLE_MODEL_ID,
    STYLE_PREPROCESSING_VERSION,
)
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.components.artist_style.contracts import (
    StyleAssessment,
    StyleSample,
    StyleScope,
)
from dataset_audit_studio.database.models import (
    Evidence,
    ModelResult,
    ReviewDecision,
)
from dataset_audit_studio.jobs.types import TaskView
from dataset_audit_studio.scoring.repository import ScoringRepository

STYLE_EVIDENCE_SOURCE = "artist_style_v1"
STYLE_REVIEW_CATEGORY = "style_outlier"


class StyleRepository:
    def __init__(self, *, project_root: Path | None = None) -> None:
        self.project_root = project_root

    def list_scopes(self, session: Session, task: TaskView) -> tuple[StyleScope, ...]:
        scoring_repository = (
            ScoringRepository(project_root=self.project_root)
            if self.project_root is not None
            else ScoringRepository()
        )
        inputs = scoring_repository.list_inputs(session, task)
        excluded_ai = set(
            session.scalars(
                select(ReviewDecision.sample_id).where(
                    ReviewDecision.task_id == task.id,
                    ReviewDecision.category == "ai_generated",
                    ReviewDecision.decision == "approved_exclude",
                    ReviewDecision.is_active.is_(True),
                    ReviewDecision.sample_id.is_not(None),
                )
            ).all()
        )
        outside_domain = set(
            session.scalars(
                select(Evidence.sample_id).where(
                    Evidence.task_id == task.id,
                    Evidence.code == "in_domain_probability",
                    Evidence.value_number.is_not(None),
                    Evidence.threshold_number.is_not(None),
                    Evidence.value_number < Evidence.threshold_number,
                )
            ).all()
        )
        grouped: dict[str, list[StyleSample]] = {}
        for item in inputs:
            if item.sample_id in excluded_ai or item.sample_id in outside_domain:
                continue
            grouped.setdefault(item.artist_scope, []).append(
                StyleSample(
                    sample_id=item.sample_id,
                    relative_path=item.relative_path,
                    artist_scope=item.artist_scope,
                    source_path=item.source_path,
                    image_path=item.image_path,
                    source_size=item.source_size,
                    source_mtime_ns=item.source_mtime_ns,
                    pixel_sha256=item.pixel_sha256,
                )
            )
        return tuple(
            StyleScope(
                scope_id=scope_id,
                samples=tuple(sorted(samples, key=lambda sample: sample.relative_path)),
            )
            for scope_id, samples in sorted(grouped.items())
        )

    @staticmethod
    def scope_config_hash(scope: StyleScope, config: StyleConfig) -> str:
        payload = {
            "scope_id": scope.scope_id,
            "samples": [
                [sample.sample_id, sample.pixel_sha256] for sample in scope.samples
            ],
            "analysis": config.analysis_payload(),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def cached_assessments(
        session: Session,
        scope: StyleScope,
        *,
        model_sha256: str,
        config_hash: str,
    ) -> tuple[StyleAssessment, ...] | None:
        rows = session.scalars(
            select(ModelResult).where(
                ModelResult.sample_id.in_([sample.sample_id for sample in scope.samples]),
                ModelResult.model_id == STYLE_MODEL_ID,
                ModelResult.model_sha256 == model_sha256,
                ModelResult.preprocessing_version == STYLE_PREPROCESSING_VERSION,
                ModelResult.config_hash == config_hash,
            )
        ).all()
        by_sample = {row.sample_id: row for row in rows}
        if set(by_sample) != {sample.sample_id for sample in scope.samples}:
            return None
        return tuple(
            StyleAssessment(**dict(by_sample[sample.sample_id].result_json))
            for sample in scope.samples
        )

    def persist_scope(
        self,
        session: Session,
        *,
        task_id: str,
        scope: StyleScope,
        assessments: tuple[StyleAssessment, ...],
        model_sha256: str,
        config_hash: str,
        config: StyleConfig,
        prepare: bool,
    ) -> None:
        if [item.sample_id for item in assessments] != [
            sample.sample_id for sample in scope.samples
        ]:
            raise ValueError("Style assessments are out of scope order")
        if prepare:
            session.execute(
                delete(Evidence).where(
                    Evidence.task_id == task_id,
                    Evidence.source == STYLE_EVIDENCE_SOURCE,
                )
            )
        sample_ids = [sample.sample_id for sample in scope.samples]
        session.execute(
            delete(Evidence).where(
                Evidence.sample_id.in_(sample_ids),
                Evidence.source == STYLE_EVIDENCE_SOURCE,
            )
        )
        for assessment in assessments:
            existing = session.scalar(
                select(ModelResult).where(
                    ModelResult.sample_id == assessment.sample_id,
                    ModelResult.model_id == STYLE_MODEL_ID,
                    ModelResult.model_sha256 == model_sha256,
                    ModelResult.preprocessing_version == STYLE_PREPROCESSING_VERSION,
                    ModelResult.config_hash == config_hash,
                )
            )
            if existing is None:
                session.add(
                    ModelResult(
                        task_id=task_id,
                        sample_id=assessment.sample_id,
                        model_id=STYLE_MODEL_ID,
                        model_sha256=model_sha256,
                        preprocessing_version=STYLE_PREPROCESSING_VERSION,
                        config_hash=config_hash,
                        result_json=asdict(assessment),
                    )
                )
            severity = (
                "high"
                if assessment.strong_outlier
                else "medium"
                if assessment.review_required
                else "info"
            )
            session.add(
                Evidence(
                    task_id=task_id,
                    sample_id=assessment.sample_id,
                    code="artist_style_score",
                    source=STYLE_EVIDENCE_SOURCE,
                    value_json=assessment.style_score,
                    threshold_json=config.minimum_style_score,
                    value_number=assessment.style_score,
                    threshold_number=config.minimum_style_score,
                    metadata_json={
                        **asdict(assessment),
                        "scope_id": scope.scope_id,
                        "scope_size": len(scope.samples),
                        "model_id": STYLE_MODEL_ID,
                        "model_sha256": model_sha256,
                        "config_hash": config_hash,
                    },
                    severity=severity,
                    review_only=True,
                    bbox_json=None,
                    algorithm_version=STYLE_PREPROCESSING_VERSION,
                )
            )
    @staticmethod
    def prepare_empty(session: Session, task_id: str) -> None:
        session.execute(
            delete(Evidence).where(
                Evidence.task_id == task_id,
                Evidence.source == STYLE_EVIDENCE_SOURCE,
            )
        )
