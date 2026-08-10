from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from dataset_audit_studio.database.models import (
    Evidence,
    ModelResult,
    ResolutionAssessment,
    Sample,
)
from dataset_audit_studio.jobs.types import TaskView
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scanner.repository import project_cache_path
from dataset_audit_studio.scoring.assets import AI_EVIDENCE_SOURCES, EVIDENCE_SOURCES
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.policy import evidence_for_result
from dataset_audit_studio.scoring.types import ComponentIdentity, SampleInput, SampleScore

AI_REVIEW_CATEGORY = "ai_generated"
SCORING_EVIDENCE_SOURCES = tuple(
    dict.fromkeys((*EVIDENCE_SOURCES.values(), *AI_EVIDENCE_SOURCES.values()))
)


class ScoringRepository:
    def __init__(self, *, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve(strict=False)

    def list_inputs(self, session: Session, task: TaskView) -> tuple[SampleInput, ...]:
        assessment_count = int(
            session.scalar(
                select(func.count())
                .select_from(ResolutionAssessment)
                .where(ResolutionAssessment.task_id == task.id)
            )
            or 0
        )
        query = select(Sample).where(
            Sample.task_id == task.id,
            Sample.scan_state == "valid",
            Sample.pixel_sha256.is_not(None),
        )
        if assessment_count:
            eligible_samples = select(ResolutionAssessment.sample_id).where(
                ResolutionAssessment.task_id == task.id,
                ResolutionAssessment.eligible.is_(True),
            )
            query = query.where(Sample.id.in_(eligible_samples))
        samples = session.scalars(query.order_by(Sample.relative_path, Sample.id)).all()

        source_root = Path(task.source_root).resolve(strict=True)
        inputs: list[SampleInput] = []
        for sample in samples:
            source_path = source_root.joinpath(
                *Path(sample.relative_path).parts
            ).resolve(strict=True)
            source_path.relative_to(source_root)
            if sample.extracted_frame_path is None:
                image_path = source_path
            else:
                image_path = self._project_cache_path(sample.extracted_frame_path)
            inputs.append(
                SampleInput(
                    sample_id=sample.id,
                    relative_path=sample.relative_path,
                    artist_scope=sample.artist_scope,
                    source_path=source_path,
                    image_path=image_path,
                    source_size=sample.source_size,
                    source_mtime_ns=sample.source_mtime_ns,
                    pixel_sha256=sample.pixel_sha256 or "",
                )
            )
        return tuple(inputs)

    def cached_results(
        self,
        session: Session,
        samples: tuple[SampleInput, ...],
        identities: dict[str, ComponentIdentity],
    ) -> dict[str, dict[str, dict]]:
        if not samples or not identities:
            return {sample.sample_id: {} for sample in samples}
        sample_ids = [sample.sample_id for sample in samples]
        rows = session.scalars(
            select(ModelResult).where(ModelResult.sample_id.in_(sample_ids))
        ).all()
        by_identity = {
            (
                identity.model_id,
                identity.model_sha256,
                identity.preprocessing_version,
                identity.config_hash,
            ): component
            for component, identity in identities.items()
        }
        cached = {sample.sample_id: {} for sample in samples}
        for row in rows:
            component = by_identity.get(
                (
                    row.model_id,
                    row.model_sha256,
                    row.preprocessing_version,
                    row.config_hash,
                )
            )
            if component is not None and row.sample_id in cached:
                cached[row.sample_id][component] = dict(row.result_json)
        return cached

    def persist_batch(
        self,
        session: Session,
        *,
        task_id: str,
        scores: tuple[SampleScore, ...],
        identities: dict[str, ComponentIdentity],
        config: ScoringConfig,
        prepare: bool,
    ) -> None:
        if prepare:
            self._prepare_task(session, task_id)
        for score in scores:
            sources = [identity.evidence_source for identity in identities.values()]
            if sources:
                session.execute(
                    delete(Evidence).where(
                        Evidence.sample_id == score.sample_id,
                        Evidence.source.in_(sources),
                    )
                )

            for component, identity in identities.items():
                result = score.results.get(component)
                if result is None:
                    raise ValueError(
                        f"Scoring result for {score.sample_id} is missing component {component}"
                    )
                self._upsert_model_result(
                    session,
                    task_id=task_id,
                    sample_id=score.sample_id,
                    identity=identity,
                    result=result,
                )
                for record in evidence_for_result(component, result, config, identity):
                    numeric = (
                        float(record.value)
                        if isinstance(record.value, (int, float))
                        and not isinstance(record.value, bool)
                        else None
                    )
                    threshold_numeric = (
                        float(record.threshold)
                        if isinstance(record.threshold, (int, float))
                        and not isinstance(record.threshold, bool)
                        else None
                    )
                    session.add(
                        Evidence(
                            task_id=task_id,
                            sample_id=score.sample_id,
                            code=record.code,
                            source=record.source,
                            value_json=record.value,
                            threshold_json=record.threshold,
                            value_number=numeric,
                            threshold_number=threshold_numeric,
                            metadata_json=record.metadata,
                            severity=record.severity,
                            review_only=record.review_only,
                            bbox_json=record.bbox,
                            algorithm_version=record.algorithm_version,
                        )
                    )
    def _project_cache_path(self, relative_path: str) -> Path:
        # Keep scanner path semantics while supporting isolated test project roots.
        if self.project_root == PROJECT_ROOT.resolve(strict=False):
            path = project_cache_path(relative_path)
            if path is None:
                raise ValueError("Extracted frame path is missing")
            return path.resolve(strict=True)
        path = self.project_root.joinpath(*Path(relative_path).parts).resolve(strict=True)
        path.relative_to(self.project_root)
        return path

    @staticmethod
    def _prepare_task(session: Session, task_id: str) -> None:
        session.execute(
            delete(Evidence).where(
                Evidence.task_id == task_id,
                Evidence.source.in_(SCORING_EVIDENCE_SOURCES),
            )
        )
    @staticmethod
    def _upsert_model_result(
        session: Session,
        *,
        task_id: str,
        sample_id: str,
        identity: ComponentIdentity,
        result: dict,
    ) -> None:
        existing = session.scalar(
            select(ModelResult).where(
                ModelResult.sample_id == sample_id,
                ModelResult.model_id == identity.model_id,
                ModelResult.model_sha256 == identity.model_sha256,
                ModelResult.preprocessing_version == identity.preprocessing_version,
                ModelResult.config_hash == identity.config_hash,
            )
        )
        if existing is None:
            session.add(
                ModelResult(
                    task_id=task_id,
                    sample_id=sample_id,
                    model_id=identity.model_id,
                    model_sha256=identity.model_sha256,
                    preprocessing_version=identity.preprocessing_version,
                    config_hash=identity.config_hash,
                    result_json=result,
                )
            )
        else:
            existing.result_json = result
