from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataset_audit_studio.adapters.dataset_workspace import DatasetWorkspaceRepository
from dataset_audit_studio.components.dataset_export.contracts import AestheticEvidence
from dataset_audit_studio.database.models import (
    Evidence,
    Sample,
)
from dataset_audit_studio.runtime import PROJECT_ROOT


class ExportRepository(DatasetWorkspaceRepository):
    def __init__(self, *, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve(strict=False)

    def load_input(self, session, task):
        return self.load_curated(session, task)

    @staticmethod
    def load_aesthetic_evidence(
        session: Session,
        task,
        sample_ids: tuple[str, ...],
    ) -> tuple[AestheticEvidence, ...]:
        if not sample_ids:
            return ()
        rows = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task.id,
                Evidence.sample_id.in_(sample_ids),
                Evidence.code == "aesthetic_score",
            )
            .order_by(Evidence.sample_id, Evidence.id)
        ).all()
        evidence: list[AestheticEvidence] = []
        for row in rows:
            metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            value = row.value_json
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                value = row.value_number
            evidence.append(
                AestheticEvidence(
                    sample_id=row.sample_id,
                    value=value,
                    source=row.source,
                    model_id=metadata.get("model_id"),
                    config_hash=metadata.get("config_hash"),
                    algorithm_version=row.algorithm_version,
                )
            )
        return tuple(evidence)

    @staticmethod
    def rewrite_paths(
        session: Session,
        task,
        retained_sample_ids: set[str],
        *,
        keep_latent: bool,
        keep_annotation: bool,
    ) -> tuple[Path, ...]:
        source_root = Path(task.source_root).resolve(strict=True)
        samples = session.scalars(select(Sample).where(Sample.task_id == task.id)).all()
        excluded = [sample for sample in samples if sample.id not in retained_sample_ids]
        paths: list[Path] = []
        for sample in excluded:
            image = source_root.joinpath(*Path(sample.relative_path).parts).resolve(strict=True)
            image.relative_to(source_root)
            paths.append(image)
            if not keep_latent:
                paths.extend(
                    path
                    for path in (
                        image.with_suffix(".npz"),
                        image.with_suffix(".safetensors"),
                    )
                    if path.is_file()
                )
        if not keep_annotation:
            for sample in excluded:
                image = source_root.joinpath(*Path(sample.relative_path).parts).resolve(strict=True)
                image.relative_to(source_root)
                for path in (image.with_suffix(".txt"), image.with_suffix(".json")):
                    if path.is_file():
                        paths.append(path)
        return tuple(paths)
