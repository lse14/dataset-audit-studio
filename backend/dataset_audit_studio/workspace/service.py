from __future__ import annotations

from pathlib import Path

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased

from dataset_audit_studio.database.enums import ArtifactState, ReviewState, TaskStatus
from dataset_audit_studio.database.models import (
    Artifact,
    ClusterMembership,
    ClusterNode,
    Evidence,
    ReviewDecision,
    Sample,
    Task,
    TaskConfig,
)
from dataset_audit_studio.database.review_overlay import active_human_overlay_by_sample
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import TaskDomainError, TaskNotFound
from dataset_audit_studio.jobs.profile import has_builtin_profile
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.workspace.constants import (
    MANUAL_EXCLUSION_CATEGORY,
    MANUAL_EXCLUSION_DECISION,
    MANUAL_INCLUDE_DECISION,
)
from dataset_audit_studio.workspace.coverage import compute_coverage_report
from dataset_audit_studio.workspace.file_access import WorkspaceFileAccess
from dataset_audit_studio.workspace.types import (
    ClusterItemView,
    ClusterListView,
    ClusterSampleListView,
    ClusterSampleView,
    CoverageReportView,
    DirectoryListingView,
    FolderItemView,
    FolderListView,
    ManualExclusionResult,
    NamedCountView,
    ReviewCountView,
    RiskEvidenceView,
    RiskListView,
    RiskSampleDetailView,
    RiskSampleListView,
    RiskSampleView,
    TaskOverviewView,
)


class InvalidManualExclusion(TaskDomainError):
    pass


_SEVERITY_BY_RANK = ("fatal", "high", "medium", "low", "info")


class WorkspaceService:
    def __init__(
        self,
        database: Database,
        *,
        project_root: Path,
        file_access: WorkspaceFileAccess | None = None,
    ) -> None:
        self.database = database
        self.project_root = project_root.resolve(strict=False)
        self.file_access = (
            file_access
            if file_access is not None
            else WorkspaceFileAccess(self.database, project_root=self.project_root)
        )

    def overview(self, task_id: str) -> TaskOverviewView:
        task = TaskService(self.database).get_task(task_id)
        with self.database.read_session() as session:
            counts = session.execute(
                select(
                    select(func.count())
                    .select_from(Sample)
                    .where(Sample.task_id == task.id)
                    .scalar_subquery()
                    .label("samples_total"),
                    select(func.count())
                    .select_from(Sample)
                    .where(Sample.task_id == task.id, Sample.scan_state == "valid")
                    .scalar_subquery()
                    .label("samples_valid"),
                    select(func.count())
                    .select_from(ClusterNode)
                    .where(ClusterNode.task_id == task.id)
                    .scalar_subquery()
                    .label("cluster_nodes"),
                    select(func.count())
                    .select_from(ClusterNode)
                    .where(
                        ClusterNode.task_id == task.id,
                        func.json_extract(ClusterNode.metadata_json, "$.is_leaf") == 1,
                    )
                    .scalar_subquery()
                    .label("leaf_clusters"),
                    select(func.count())
                    .select_from(Artifact)
                    .where(
                        Artifact.task_id == task.id,
                        Artifact.state == ArtifactState.READY.value,
                    )
                    .scalar_subquery()
                    .label("ready_artifacts"),
                )
            ).one()
            evidence_rows = session.execute(
                select(Evidence.code, func.count())
                .where(Evidence.task_id == task.id)
                .group_by(Evidence.code)
                .order_by(func.count().desc(), Evidence.code)
            ).all()
            review_rows = session.execute(
                select(ReviewDecision.category, ReviewDecision.decision, func.count())
                .where(
                    ReviewDecision.task_id == task.id,
                    ReviewDecision.is_active.is_(True),
                )
                .group_by(ReviewDecision.category, ReviewDecision.decision)
                .order_by(ReviewDecision.category, ReviewDecision.decision)
            ).all()
        return TaskOverviewView(
            samples_total=int(counts.samples_total or 0),
            samples_valid=int(counts.samples_valid or 0),
            cluster_nodes=int(counts.cluster_nodes or 0),
            leaf_clusters=int(counts.leaf_clusters or 0),
            ready_artifacts=int(counts.ready_artifacts or 0),
            evidence_codes=tuple(
                NamedCountView(name=str(name), count=int(count)) for name, count in evidence_rows
            ),
            review_counts=tuple(
                ReviewCountView(
                    category=str(category),
                    decision=str(decision),
                    count=int(count),
                )
                for category, decision, count in review_rows
            ),
        )

    def coverage(self, task_id: str, *, resolution: int) -> CoverageReportView:
        task = TaskService(self.database).get_task(task_id)
        with self.database.read_session() as session:
            current = session.get(Task, task.id)
            if current is None:
                raise TaskNotFound(f"Task not found: {task_id}")
            return compute_coverage_report(session, current, resolution)

    def folders(self, task_id: str) -> FolderListView:
        TaskService(self.database).get_task(task_id)
        leaf = func.json_extract(ClusterNode.metadata_json, "$.is_leaf") == 1
        with self.database.read_session() as session:
            sample_rows = session.execute(
                select(Sample.artist_scope, func.count(Sample.id))
                .where(Sample.task_id == task_id)
                .group_by(Sample.artist_scope)
            ).all()
            artist_cluster_rows = session.execute(
                select(ClusterNode.scope_id, func.count(ClusterNode.id))
                .where(
                    ClusterNode.task_id == task_id,
                    ClusterNode.scope_kind.in_(("artist", "concept")),
                    leaf,
                )
                .group_by(ClusterNode.scope_id)
            ).all()
            global_cluster_rows = session.execute(
                select(
                    Sample.artist_scope,
                    func.count(func.distinct(ClusterNode.id)),
                )
                .select_from(ClusterNode)
                .join(
                    ClusterMembership,
                    and_(
                        ClusterMembership.cluster_id == ClusterNode.id,
                        ClusterMembership.task_id == task_id,
                    ),
                )
                .join(
                    Sample,
                    and_(
                        Sample.id == ClusterMembership.sample_id,
                        Sample.task_id == task_id,
                    ),
                )
                .where(
                    ClusterNode.task_id == task_id,
                    ClusterNode.scope_kind == "global",
                    leaf,
                )
                .group_by(Sample.artist_scope)
            ).all()
            risk_rows = session.execute(
                select(
                    Sample.artist_scope,
                    func.count(func.distinct(Evidence.sample_id)),
                    func.count(Evidence.id),
                )
                .select_from(Sample)
                .join(
                    Evidence,
                    and_(
                        Evidence.sample_id == Sample.id,
                        Evidence.task_id == task_id,
                    ),
                )
                .where(Sample.task_id == task_id)
                .group_by(Sample.artist_scope)
            ).all()
        sample_counts = {str(scope): int(count) for scope, count in sample_rows}
        cluster_counts = {str(scope): int(count) for scope, count in artist_cluster_rows}
        for scope, count in global_cluster_rows:
            key = str(scope)
            cluster_counts[key] = cluster_counts.get(key, 0) + int(count)
        risk_counts = {
            str(scope): (int(samples), int(evidence)) for scope, samples, evidence in risk_rows
        }
        return FolderListView(
            items=tuple(
                FolderItemView(
                    folder_id=scope,
                    display_name="根目录" if scope == "__root__" else scope,
                    sample_count=sample_counts[scope],
                    leaf_cluster_count=cluster_counts.get(scope, 0),
                    risk_sample_count=risk_counts.get(scope, (0, 0))[0],
                    risk_evidence_count=risk_counts.get(scope, (0, 0))[1],
                )
                for scope in sorted(sample_counts, key=str.casefold)
            )
        )

    def clusters(
        self,
        task_id: str,
        *,
        offset: int,
        limit: int,
        folder: str | None = None,
    ) -> ClusterListView:
        TaskService(self.database).get_task(task_id)
        leaf = func.json_extract(ClusterNode.metadata_json, "$.is_leaf") == 1
        with self.database.read_session() as session:
            self._validate_folder(session, task_id, folder)
            filters = [ClusterNode.task_id == task_id, leaf]
            if folder is not None:
                folder_member = (
                    select(1)
                    .select_from(ClusterMembership)
                    .join(
                        Sample,
                        and_(
                            Sample.id == ClusterMembership.sample_id,
                            Sample.task_id == task_id,
                        ),
                    )
                    .where(
                        ClusterMembership.task_id == task_id,
                        ClusterMembership.cluster_id == ClusterNode.id,
                        Sample.artist_scope == folder,
                    )
                    .correlate(ClusterNode)
                    .exists()
                )
                filters.append(
                    or_(
                        and_(
                            ClusterNode.scope_kind.in_(("artist", "concept")),
                            ClusterNode.scope_id == folder,
                        ),
                        and_(
                            ClusterNode.scope_kind == "global",
                            folder_member,
                        ),
                    )
                )
            total = self._count(
                session,
                ClusterNode,
                *filters,
            )
            rows = session.scalars(
                select(ClusterNode)
                .where(*filters)
                .order_by(
                    ClusterNode.size.desc(),
                    ClusterNode.scope_id,
                    ClusterNode.cluster_key,
                )
                .offset(offset)
                .limit(limit)
            ).all()
            representative_ids = {
                str(row.metadata_json.get("representative_sample_id"))
                for row in rows
                if row.metadata_json.get("representative_sample_id")
            }
            representative_samples = (
                session.scalars(
                    select(Sample).where(
                        Sample.task_id == task_id,
                        Sample.id.in_(representative_ids),
                    )
                ).all()
                if representative_ids
                else ()
            )
            representatives = {
                sample.id: (sample.relative_path, sample.artist_scope)
                for sample in representative_samples
            }
            representative_by_cluster: dict[str, tuple[str, str]] = {}
            for row in rows:
                representative_id = row.metadata_json.get("representative_sample_id")
                sample = representatives.get(str(representative_id))
                if representative_id and sample and (folder is None or sample[1] == folder):
                    representative_by_cluster[row.id] = (
                        str(representative_id),
                        sample[0],
                    )
            cluster_ids = [row.id for row in rows]
            folder_sizes: dict[str, int] = {}
            if folder is not None and cluster_ids:
                size_rows = session.execute(
                    select(ClusterMembership.cluster_id, func.count())
                    .join(
                        Sample,
                        and_(
                            Sample.id == ClusterMembership.sample_id,
                            Sample.task_id == task_id,
                        ),
                    )
                    .where(
                        ClusterMembership.task_id == task_id,
                        ClusterMembership.cluster_id.in_(cluster_ids),
                        Sample.artist_scope == folder,
                    )
                    .group_by(ClusterMembership.cluster_id)
                ).all()
                folder_sizes = {str(cluster_id): int(count) for cluster_id, count in size_rows}
                fallback_rows = session.execute(
                    select(
                        ClusterMembership.cluster_id,
                        Sample.id,
                        Sample.relative_path,
                    )
                    .join(
                        Sample,
                        and_(
                            Sample.id == ClusterMembership.sample_id,
                            Sample.task_id == task_id,
                        ),
                    )
                    .where(
                        ClusterMembership.task_id == task_id,
                        ClusterMembership.cluster_id.in_(cluster_ids),
                        Sample.artist_scope == folder,
                    )
                    .order_by(
                        ClusterMembership.cluster_id,
                        ClusterMembership.is_representative.desc(),
                        case((ClusterMembership.score.is_(None), 1), else_=0),
                        ClusterMembership.score.desc(),
                        Sample.relative_path,
                        Sample.id,
                    )
                ).all()
                for cluster_id, sample_id, relative_path in fallback_rows:
                    representative_by_cluster.setdefault(
                        str(cluster_id),
                        (str(sample_id), str(relative_path)),
                    )
        return ClusterListView(
            items=tuple(
                ClusterItemView(
                    cluster_id=row.id,
                    cluster_key=row.cluster_key,
                    scope_kind=row.scope_kind,
                    scope_id=row.scope_id,
                    level=row.level,
                    size=row.size,
                    total_size=row.size,
                    folder_size=(
                        row.size
                        if folder is None
                        else (
                            row.size
                            if row.scope_kind in {"artist", "concept"} and row.scope_id == folder
                            else folder_sizes.get(row.id, 0)
                        )
                    ),
                    representative_sample_id=(
                        representative_by_cluster.get(row.id, (None, None))[0]
                    ),
                    representative_path=(representative_by_cluster.get(row.id, (None, None))[1]),
                )
                for row in rows
            ),
            total=total,
            offset=offset,
            limit=limit,
        )

    def cluster_samples(
        self,
        task_id: str,
        cluster_id: str,
        *,
        offset: int,
        limit: int,
        folder: str | None = None,
    ) -> ClusterSampleListView:
        TaskService(self.database).get_task(task_id)
        leaf = func.json_extract(ClusterNode.metadata_json, "$.is_leaf") == 1
        with self.database.read_session() as session:
            self._validate_folder(session, task_id, folder)
            cluster = session.scalar(
                select(ClusterNode).where(
                    ClusterNode.task_id == task_id,
                    ClusterNode.id == cluster_id,
                    leaf,
                )
            )
            if cluster is None:
                raise TaskNotFound(f"Leaf cluster not found for task: {cluster_id}")
            filters = [
                ClusterMembership.task_id == task_id,
                ClusterMembership.cluster_id == cluster_id,
                Sample.task_id == task_id,
            ]
            if folder is not None:
                filters.append(Sample.artist_scope == folder)
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(ClusterMembership)
                    .join(Sample, Sample.id == ClusterMembership.sample_id)
                    .where(*filters)
                )
                or 0
            )
            manually_excluded = self._manual_excluded(task_id, Sample.id)
            rows = session.execute(
                select(
                    Sample,
                    ClusterMembership.score,
                    ClusterMembership.is_representative,
                    manually_excluded.label("manually_excluded"),
                )
                .select_from(ClusterMembership)
                .join(Sample, Sample.id == ClusterMembership.sample_id)
                .where(*filters)
                .order_by(
                    case((ClusterMembership.score.is_(None), 1), else_=0),
                    ClusterMembership.score.desc(),
                    Sample.relative_path,
                    Sample.id,
                )
                .offset(offset)
                .limit(limit)
            ).all()
        return ClusterSampleListView(
            items=tuple(
                ClusterSampleView(
                    sample_id=sample.id,
                    relative_path=sample.relative_path,
                    artist_scope=sample.artist_scope,
                    score=float(score) if score is not None else None,
                    is_representative=bool(is_representative),
                    manually_excluded=bool(manually_excluded),
                )
                for sample, score, is_representative, manually_excluded in rows
            ),
            total=total,
            offset=offset,
            limit=limit,
        )

    def risks(
        self,
        task_id: str,
        *,
        offset: int,
        limit: int,
        code: str | None,
    ) -> RiskListView:
        TaskService(self.database).get_task(task_id)
        filters = [Evidence.task_id == task_id]
        if code is not None:
            filters.append(Evidence.code == code)
        severity_order = case(
            (Evidence.severity == "fatal", 0),
            (Evidence.severity == "high", 1),
            (Evidence.severity == "medium", 2),
            (Evidence.severity == "low", 3),
            else_=4,
        )
        with self.database.read_session() as session:
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(Evidence)
                    .join(Sample, Sample.id == Evidence.sample_id)
                    .where(*filters)
                )
                or 0
            )
            rows = session.execute(
                select(Evidence, Sample)
                .join(Sample, Sample.id == Evidence.sample_id)
                .where(*filters)
                .order_by(severity_order, Sample.relative_path, Evidence.code)
                .offset(offset)
                .limit(limit)
            ).all()
        return RiskListView(
            items=tuple(
                RiskEvidenceView(
                    evidence_id=evidence.id,
                    sample_id=sample.id,
                    relative_path=sample.relative_path,
                    artist_scope=sample.artist_scope,
                    code=evidence.code,
                    source=evidence.source,
                    value=evidence.value_json,
                    threshold=evidence.threshold_json,
                    value_number=evidence.value_number,
                    threshold_number=evidence.threshold_number,
                    severity=evidence.severity,
                    review_only=evidence.review_only,
                    bbox=evidence.bbox_json,
                    metadata=dict(evidence.metadata_json),
                )
                for evidence, sample in rows
            ),
            total=total,
            offset=offset,
            limit=limit,
        )

    def risk_samples(
        self,
        task_id: str,
        *,
        offset: int,
        limit: int,
        code: str | None,
        folder: str | None = None,
        severity: str | None = None,
        decision: str = "all",
    ) -> RiskSampleListView:
        TaskService(self.database).get_task(task_id)
        self._validate_audit_decision(decision)
        severity_rank = self._severity_rank()
        with self.database.read_session() as session:
            self._validate_folder(session, task_id, folder)
            filters = [
                Sample.task_id == task_id,
                Evidence.task_id == task_id,
            ]
            if folder is not None:
                filters.append(Sample.artist_scope == folder)
            if code is not None:
                filters.append(Evidence.code == code)
            if severity is not None:
                filters.append(Evidence.severity == severity)
            active = None
            if decision != "all":
                active = aliased(ReviewDecision)
                filters.append(self._audit_decision_filter(active, decision))
            total_statement = (
                select(func.count(func.distinct(Sample.id)))
                .select_from(Sample)
                .join(Evidence, Evidence.sample_id == Sample.id)
            )
            if active is not None:
                total_statement = total_statement.outerjoin(
                    active,
                    self._active_human_overlay_join(active),
                )
            total = int(
                session.scalar(total_statement.where(*filters))
                or 0
            )
            manually_excluded = self._manual_excluded(task_id, Sample.id)
            rows_statement = (
                select(
                    Sample.id,
                    Sample.relative_path,
                    Sample.artist_scope,
                    func.min(severity_rank).label("severity_rank"),
                    func.count(Evidence.id).label("evidence_count"),
                    manually_excluded.label("manually_excluded"),
                )
                .select_from(Sample)
                .join(Evidence, Evidence.sample_id == Sample.id)
            )
            if active is not None:
                rows_statement = rows_statement.outerjoin(
                    active,
                    self._active_human_overlay_join(active),
                )
            rows = session.execute(
                rows_statement
                .where(*filters)
                .group_by(Sample.id, Sample.relative_path, Sample.artist_scope)
                .order_by(
                    func.min(severity_rank),
                    Sample.relative_path,
                    Sample.id,
                )
                .offset(offset)
                .limit(limit)
            ).all()
            sample_ids = [str(row.id) for row in rows]
            evidence_code_filters = [
                Evidence.task_id == task_id,
                Evidence.sample_id.in_(sample_ids),
            ]
            if code is not None:
                evidence_code_filters.append(Evidence.code == code)
            if severity is not None:
                evidence_code_filters.append(Evidence.severity == severity)
            code_rows = (
                session.execute(
                    select(Evidence.sample_id, Evidence.code)
                    .where(*evidence_code_filters)
                    .order_by(Evidence.sample_id, Evidence.code)
                ).all()
                if sample_ids
                else ()
            )
        codes_by_sample: dict[str, list[str]] = {}
        for sample_id, evidence_code in code_rows:
            values = codes_by_sample.setdefault(str(sample_id), [])
            value = str(evidence_code)
            if value not in values:
                values.append(value)
        return RiskSampleListView(
            items=tuple(
                RiskSampleView(
                    sample_id=str(row.id),
                    relative_path=str(row.relative_path),
                    artist_scope=str(row.artist_scope),
                    highest_severity=self._severity_name(int(row.severity_rank)),
                    evidence_count=int(row.evidence_count),
                    evidence_codes=tuple(codes_by_sample.get(str(row.id), ())),
                    manually_excluded=bool(row.manually_excluded),
                )
                for row in rows
            ),
            total=total,
            offset=offset,
            limit=limit,
        )

    def risk_sample_detail(
        self,
        task_id: str,
        sample_id: str,
        *,
        code: str | None,
        severity: str | None = None,
    ) -> RiskSampleDetailView:
        TaskService(self.database).get_task(task_id)
        with self.database.read_session() as session:
            sample = session.scalar(
                select(Sample).where(
                    Sample.task_id == task_id,
                    Sample.id == sample_id,
                )
            )
            if sample is None:
                raise TaskNotFound(f"Risk sample not found for task: {sample_id}")
            filters = [
                Evidence.task_id == task_id,
                Evidence.sample_id == sample_id,
            ]
            if code is not None:
                filters.append(Evidence.code == code)
            if severity is not None:
                filters.append(Evidence.severity == severity)
            evidence_rows = session.scalars(
                select(Evidence)
                .where(*filters)
                .order_by(self._severity_rank(), Evidence.code, Evidence.id)
            ).all()
            if not evidence_rows:
                raise TaskNotFound(f"Risk evidence not found for sample: {sample_id}")
            manually_excluded = bool(
                session.scalar(
                    select(func.count())
                    .select_from(ReviewDecision)
                    .where(
                        ReviewDecision.task_id == task_id,
                        ReviewDecision.sample_id == sample_id,
                        ReviewDecision.category == MANUAL_EXCLUSION_CATEGORY,
                        ReviewDecision.decision == MANUAL_EXCLUSION_DECISION,
                        ReviewDecision.is_active.is_(True),
                    )
                )
            )
        return RiskSampleDetailView(
            sample_id=sample.id,
            relative_path=sample.relative_path,
            artist_scope=sample.artist_scope,
            manually_excluded=manually_excluded,
            evidence=tuple(
                self._risk_evidence_view(evidence, sample) for evidence in evidence_rows
            ),
        )

    def set_manual_exclusions(
        self,
        task_id: str,
        *,
        sample_ids: list[str],
        excluded: bool,
        context: dict[str, object],
    ) -> ManualExclusionResult:
        unique_ids = list(dict.fromkeys(sample_ids))
        if not unique_ids or len(unique_ids) > 5000:
            raise InvalidManualExclusion(
                "Manual exclusion requires between 1 and 5000 explicit samples"
            )
        with self.database.write_session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(f"Task not found: {task_id}")
            if not self._manual_exclusion_window(session, task):
                raise InvalidManualExclusion(
                    "Manual exclusions can only change during evidence review"
                )
            rows = session.execute(
                select(Sample.id).where(
                    Sample.task_id == task_id,
                    Sample.id.in_(unique_ids),
                )
            ).all()
            found_ids = {str(row.id) for row in rows}
            missing_ids = [sample_id for sample_id in unique_ids if sample_id not in found_ids]
            if missing_ids:
                raise InvalidManualExclusion(
                    f"Samples do not belong to task: {', '.join(missing_ids[:5])}"
                )
            active_by_sample = active_human_overlay_by_sample(
                session,
                task_id=task_id,
                sample_ids=unique_ids,
            )
            desired = MANUAL_EXCLUSION_DECISION if excluded else MANUAL_INCLUDE_DECISION
            changed = 0
            for sample_id in unique_ids:
                active = active_by_sample.get(sample_id, ())
                previous = active[0] if active else None
                if previous is not None and previous.decision == desired:
                    continue
                for existing in active:
                    existing.is_active = False
                session.add(
                    ReviewDecision(
                        task_id=task_id,
                        sample_id=sample_id,
                        scope_type="sample",
                        scope_id=sample_id,
                        category=MANUAL_EXCLUSION_CATEGORY,
                        decision=desired,
                        source="human",
                        context_json={
                            "excluded": excluded,
                            "request": dict(context),
                        },
                        supersedes_id=previous.id if previous is not None else None,
                        is_active=True,
                    )
                )
                changed += 1
        return ManualExclusionResult(
            selected=len(unique_ids),
            changed=changed,
            excluded=excluded,
        )

    @staticmethod
    def _manual_exclusion_window(session, task: Task) -> bool:
        if task.status == TaskStatus.EVIDENCE_REVIEW.value:
            return True
        if task.status != TaskStatus.PAUSED.value:
            return False
        if task.resume_state == TaskStatus.EVIDENCE_REVIEW.value:
            return True
        if task.resume_state != TaskStatus.EXPORTING.value:
            return False
        config = session.scalar(
            select(TaskConfig.config_json).where(
                TaskConfig.task_id == task.id,
                TaskConfig.revision == task.current_config_revision,
            )
        )
        return isinstance(config, dict) and has_builtin_profile(config)

    @staticmethod
    def _validate_folder(session, task_id: str, folder: str | None) -> None:
        if folder is None:
            return
        exists_for_task = session.scalar(
            select(func.count())
            .select_from(Sample)
            .where(
                Sample.task_id == task_id,
                Sample.artist_scope == folder,
            )
        )
        if not exists_for_task:
            raise TaskNotFound(f"Folder not found for task: {folder}")

    @staticmethod
    def _manual_excluded(task_id: str, sample_id):
        return (
            select(1)
            .select_from(ReviewDecision)
            .where(
                ReviewDecision.task_id == task_id,
                ReviewDecision.sample_id == sample_id,
                ReviewDecision.category == MANUAL_EXCLUSION_CATEGORY,
                ReviewDecision.decision == MANUAL_EXCLUSION_DECISION,
                ReviewDecision.is_active.is_(True),
            )
            .correlate_except(ReviewDecision)
            .exists()
        )

    @staticmethod
    def _severity_rank():
        return case(
            (Evidence.severity == "fatal", 0),
            (Evidence.severity == "high", 1),
            (Evidence.severity == "medium", 2),
            (Evidence.severity == "low", 3),
            else_=4,
        )

    @staticmethod
    def _severity_name(rank: int) -> str:
        return _SEVERITY_BY_RANK[min(max(rank, 0), len(_SEVERITY_BY_RANK) - 1)]

    @staticmethod
    def _risk_evidence_view(evidence: Evidence, sample: Sample) -> RiskEvidenceView:
        return RiskEvidenceView(
            evidence_id=evidence.id,
            sample_id=sample.id,
            relative_path=sample.relative_path,
            artist_scope=sample.artist_scope,
            code=evidence.code,
            source=evidence.source,
            value=evidence.value_json,
            threshold=evidence.threshold_json,
            value_number=evidence.value_number,
            threshold_number=evidence.threshold_number,
            severity=evidence.severity,
            review_only=evidence.review_only,
            bbox=evidence.bbox_json,
            metadata=dict(evidence.metadata_json),
        )

    def thumbnail(self, task_id: str, sample_id: str, *, size: int) -> Path:
        return self.file_access.thumbnail(task_id, sample_id, size=size)

    def media(self, task_id: str, sample_id: str):
        return self.file_access.media(task_id, sample_id)

    def directories(self, raw_path: str | None) -> DirectoryListingView:
        return self.file_access.directories(raw_path)

    @staticmethod
    def _count(session, model, *filters) -> int:
        return int(session.scalar(select(func.count()).select_from(model).where(*filters)) or 0)

    @staticmethod
    def _validate_audit_decision(decision: str) -> None:
        if decision == "all" or decision in {state.value for state in ReviewState}:
            return
        raise TaskDomainError(f"Unsupported audit decision: {decision}")

    @staticmethod
    def _active_human_overlay_join(active):
        return and_(
            active.task_id == Sample.task_id,
            active.sample_id == Sample.id,
            active.source == "human",
            active.decision.in_(
                (
                    ReviewState.APPROVED_KEEP.value,
                    ReviewState.APPROVED_EXCLUDE.value,
                )
            ),
            active.is_active.is_(True),
        )

    @staticmethod
    def _audit_decision_filter(active, decision: str):
        if decision == ReviewState.PENDING_REVIEW.value:
            return active.id.is_(None)
        return active.decision == decision
