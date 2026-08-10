from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataset_audit_studio.core.dataset_artifacts import (
    DatasetSample,
    DatasetSlice,
    DatasetWorkspace,
)
from dataset_audit_studio.database.models import (
    Evidence,
    ReviewDecision,
    Sample,
)
from dataset_audit_studio.jobs.errors import LegacyTaskConfigUnsupported
from dataset_audit_studio.jobs.profile import require_builtin_profile
from dataset_audit_studio.jobs.types import TaskView
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scoring.assets import EVIDENCE_SOURCES, PREPROCESSING_VERSIONS
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.workspace.constants import (
    MANUAL_EXCLUSION_CATEGORY,
    MANUAL_EXCLUSION_DECISION,
)
from dataset_audit_studio.workspace.curated import (
    AestheticScoreRecord,
    CuratedMembership,
    compute_curated_members,
)


class DatasetWorkspaceRepository:
    def __init__(self, *, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve(strict=False)

    def load(
        self,
        session: Session,
        task: TaskView,
        *,
        apply_manual_exclusions: bool = True,
    ) -> DatasetWorkspace:
        try:
            require_builtin_profile(task.config)
        except LegacyTaskConfigUnsupported:
            raise
        manually_excluded_ids = select(ReviewDecision.sample_id).where(
            ReviewDecision.task_id == task.id,
            ReviewDecision.category == MANUAL_EXCLUSION_CATEGORY,
            ReviewDecision.decision == MANUAL_EXCLUSION_DECISION,
            ReviewDecision.is_active.is_(True),
            ReviewDecision.sample_id.is_not(None),
        )
        rows = session.scalars(
            select(Sample)
            .where(
                Sample.task_id == task.id,
                Sample.scan_state == "valid",
                (
                    Sample.id.not_in(manually_excluded_ids)
                    if apply_manual_exclusions
                    else True
                ),
            )
            .order_by(Sample.relative_path, Sample.id)
        ).all()
        source_root = Path(task.source_root).resolve(strict=True)
        samples: list[DatasetSample] = []
        for row in rows:
            source_path = source_root.joinpath(*Path(row.relative_path).parts).resolve(strict=True)
            source_path.relative_to(source_root)
            if row.extracted_frame_path is None:
                image_path = source_path
            else:
                image_path = self.project_root.joinpath(
                    *Path(row.extracted_frame_path).parts
                ).resolve(strict=True)
                image_path.relative_to(self.project_root)
            samples.append(
                DatasetSample(
                    sample_id=row.id,
                    relative_path=row.relative_path,
                    artist_scope=row.artist_scope,
                    source_path=source_path,
                    image_path=image_path,
                    source_size=row.source_size,
                    source_mtime_ns=row.source_mtime_ns,
                    source_sha256=row.source_sha256,
                    pixel_sha256=row.pixel_sha256 or "",
                    export_requires_render=row.export_requires_render,
                )
            )
        resolutions = self._configured_resolutions(task)
        grouped: dict[int, list[str]] = defaultdict(list)
        for row in rows:
            width = row.display_width or row.encoded_width
            height = row.display_height or row.encoded_height
            if not isinstance(width, int) or not isinstance(height, int):
                continue
            for resolution in resolutions:
                if width * height >= resolution * resolution:
                    grouped[resolution].append(row.id)
        datasets = tuple(
            DatasetSlice(stage=1, resolution=resolution, sample_ids=tuple(sample_ids))
            for resolution, sample_ids in sorted(grouped.items())
            if sample_ids
        )
        return DatasetWorkspace(samples=tuple(samples), datasets=datasets)

    @staticmethod
    def _configured_resolutions(task: TaskView) -> tuple[int, ...]:
        scan = task.config.get("scan")
        values = scan.get("resolutions") if isinstance(scan, dict) else None
        if not isinstance(values, list):
            return (512, 768, 1024, 1216, 1536)
        supported = {512, 768, 1024, 1216, 1536}
        selected = tuple(sorted({int(value) for value in values if value in supported}))
        return selected or (512, 768, 1024, 1216, 1536)

    def load_curated(self, session: Session, task: TaskView) -> DatasetWorkspace:
        """Apply the curated/export overlay while preserving broad memberships."""

        broad = self.load(session, task, apply_manual_exclusions=False)
        broad_ids = tuple(
            dict.fromkeys(
                sample_id for dataset in broad.datasets for sample_id in dataset.sample_ids
            )
        )
        membership = self.curated_memberships(
            session,
            task,
            broad_sample_ids=broad_ids,
            include_human_decisions=True,
        )
        retained = {item.sample_id for item in membership if item.included}
        samples = tuple(sample for sample in broad.samples if sample.sample_id in retained)
        datasets = tuple(
            DatasetSlice(
                stage=dataset.stage,
                resolution=dataset.resolution,
                sample_ids=tuple(
                    sample_id for sample_id in dataset.sample_ids if sample_id in retained
                ),
            )
            for dataset in broad.datasets
            if any(sample_id in retained for sample_id in dataset.sample_ids)
        )
        return DatasetWorkspace(samples=samples, datasets=datasets)

    def curated_memberships(
        self,
        session: Session,
        task: TaskView,
        *,
        broad_sample_ids: tuple[str, ...] | None = None,
        include_human_decisions: bool = True,
    ) -> tuple[CuratedMembership, ...]:
        """Return curated overlay decisions without mutating broad membership."""

        if broad_sample_ids is None:
            broad = self.load(session, task, apply_manual_exclusions=False)
            broad_sample_ids = tuple(
                dict.fromkeys(
                    sample_id for dataset in broad.datasets for sample_id in dataset.sample_ids
                )
            )
        return self._compute_curated_memberships(
            session,
            task,
            broad_sample_ids,
            include_human_decisions=include_human_decisions,
        )

    def _compute_curated_memberships(
        self,
        session: Session,
        task: TaskView,
        broad_ids: tuple[str, ...],
        *,
        include_human_decisions: bool,
    ) -> tuple[CuratedMembership, ...]:
        components = task.config.get("components", {})
        if not isinstance(components, dict):
            raise ValueError("legacy_task_config_unsupported: complete components are required")
        export_entry = components.get("export.dataset", {})
        export_config = export_entry.get("config", {}) if isinstance(export_entry, dict) else {}
        aesthetic_entry = components.get("score.aesthetic_domain", {})
        aesthetic_enabled = (
            bool(aesthetic_entry.get("enabled")) if isinstance(aesthetic_entry, dict) else False
        )
        minimum = (
            export_config.get("aesthetic_minimum")
            if isinstance(export_config, dict)
            else None
        )
        if minimum is not None:
            if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
                raise ValueError("Aesthetic minimum must be numeric")
            if not math.isfinite(float(minimum)) or not 1.0 <= float(minimum) <= 5.0:
                raise ValueError("Aesthetic minimum must be finite and between 1 and 5")
            if not aesthetic_enabled:
                raise ValueError("Aesthetic curation requires enabled aesthetic scoring")

        aesthetic_identity = None
        if minimum is not None:
            scoring = ScoringConfig.from_task_config(task.config)
            if not scoring.aesthetic.enabled:
                raise ValueError("Aesthetic curation requires enabled aesthetic scoring")
            aesthetic_identity = {
                "source": EVIDENCE_SOURCES["aesthetic"],
                "model_id": scoring.aesthetic.model_id,
                "config_hash": scoring.inference_config_hash("aesthetic"),
                "algorithm_version": PREPROCESSING_VERSIONS["aesthetic"],
            }

        evidence_by_sample: dict[str, list[AestheticScoreRecord]] = {}
        if broad_ids:
            rows = session.scalars(
                select(Evidence).where(
                    Evidence.task_id == task.id,
                    Evidence.sample_id.in_(broad_ids),
                    Evidence.code == "aesthetic_score",
                )
            ).all()
            for row in rows:
                metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
                value = row.value_json
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    value = row.value_number
                evidence_by_sample.setdefault(row.sample_id, []).append(
                    AestheticScoreRecord(
                        value=value,
                        source=row.source,
                        model_id=metadata.get("model_id"),
                        config_hash=metadata.get("config_hash"),
                        algorithm_version=row.algorithm_version,
                    )
                )

        human_decisions: dict[str, str] = {}
        if broad_ids and include_human_decisions:
            active = session.scalars(
                select(ReviewDecision)
                .where(
                    ReviewDecision.task_id == task.id,
                    ReviewDecision.sample_id.in_(broad_ids),
                    ReviewDecision.source == "human",
                    ReviewDecision.is_active.is_(True),
                    ReviewDecision.decision.in_(("approved_keep", "approved_exclude")),
                )
                .order_by(ReviewDecision.created_at.desc(), ReviewDecision.id.desc())
            ).all()
            for row in active:
                if row.sample_id is not None and row.sample_id not in human_decisions:
                    human_decisions[row.sample_id] = row.decision

        membership = compute_curated_members(
            broad_ids,
            aesthetic_evidence=evidence_by_sample,
            aesthetic_minimum=minimum,
            aesthetic_identity=aesthetic_identity,
            human_decisions=human_decisions,
        )
        return membership
