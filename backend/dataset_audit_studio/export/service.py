from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dataset_audit_studio.components.dataset_export.config import DatasetExportConfig
from dataset_audit_studio.components.dataset_export.contracts import ExportPlan, ExportSummary
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.export.repository import ExportRepository
from dataset_audit_studio.export.rewrite import (
    execute_rewrite,
    restore_backup,
    rewrite_preview_digest,
)
from dataset_audit_studio.jobs.errors import InvalidTaskTransition, StaleWorkerToken
from dataset_audit_studio.jobs.profile import has_builtin_profile
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT

COMPONENT_ID = "export.dataset"


def workspace_digest(workspace) -> str:
    payload = [
        [sample.sample_id, sample.relative_path, sample.source_sha256]
        for sample in workspace.samples
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


class DatasetExporter:
    """Run the retained destructive rewrite workflow only.

    Copy exports are owned exclusively by ``ExportRunService`` and its executor.
    """

    def __init__(
        self,
        tasks: TaskService,
        *,
        repository: ExportRepository | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self.tasks = tasks
        self.project_root = project_root.resolve(strict=False)
        self.repository = repository or ExportRepository(project_root=project_root)

    def run(
        self,
        token: WorkerToken,
        *,
        finalize_phase: bool = True,
    ) -> ExportSummary:
        task = self.tasks.get_task(token.task_id)
        if task.status != TaskStatus.EXPORTING.value:
            raise InvalidTaskTransition(
                f"Dataset exporter requires exporting status, got {task.status}"
            )
        if not has_builtin_profile(task.config):
            raise InvalidTaskTransition(
                "legacy_task_config_unsupported: profile-free tasks cannot execute"
            )
        config = DatasetExportConfig.from_task_config(task.config)
        if config.mode != "rewrite":
            raise InvalidTaskTransition("copy export must use ExportRunService")
        with self.tasks.database.read_session() as session:
            workspace = self.repository.load_input(session, task)
        return self._run_rewrite(
            token,
            task,
            workspace,
            config,
            finalize_phase=finalize_phase,
        )

    def _run_rewrite(
        self,
        token: WorkerToken,
        task,
        workspace,
        config: DatasetExportConfig,
        *,
        finalize_phase: bool,
    ) -> ExportSummary:
        source_root = Path(task.source_root).resolve(strict=True)
        retained = {sample.sample_id for sample in workspace.samples}
        with self.tasks.database.read_session() as session:
            paths = self.repository.rewrite_paths(
                session,
                task,
                retained,
                keep_latent=config.keep_latent_files,
                keep_annotation=config.keep_annotation_files,
            )
        input_digest = workspace_digest(workspace)
        expected_digest = rewrite_preview_digest(
            task_id=task.id,
            config_hash=task.config_hash,
            config_revision=task.current_config_revision,
            curated_sample_ids=tuple(sample.sample_id for sample in workspace.samples),
            paths=paths,
            source_root=source_root,
        )
        if config.backup_enabled is not True:
            raise ValueError("backup_enabled must be true for rewrite")
        confirmed = any(
            event.event_type == "rewrite_preview_confirmed"
            and event.payload.get("preview_digest") == expected_digest
            and event.payload.get("config_revision") == task.current_config_revision
            and event.payload.get("config_hash") == task.config_hash
            for event in self.tasks.list_events(task.id)
        )
        if not confirmed:
            raise ValueError("Rewrite requires explicit preview confirmation")
        result = execute_rewrite(
            source_root,
            task.id,
            paths,
            backup_enabled=config.backup_enabled,
        )
        cursor = {
            "modular_exporting": True,
            "component_id": COMPONENT_ID,
            "input_digest": input_digest,
            "rewrite": True,
            "deleted_files": result["deleted_files"],
            "backup_path": result["backup_path"],
            "component_complete": True,
        }
        try:
            checkpoints = [
                checkpoint
                for checkpoint in self.tasks.list_checkpoints(
                    task.id,
                    phase=TaskStatus.EXPORTING.value,
                )
                if checkpoint.config_hash == task.config_hash
            ]
            batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
            status = self.tasks.commit_batch(
                token,
                phase=TaskStatus.EXPORTING,
                config_hash=task.config_hash,
                batch_index=batch_index,
                completed_items=len(paths),
                progress_total=len(paths),
                cursor=cursor,
                lease_seconds=300,
            ).task.status
        except Exception:
            backup_path = result["backup_path"]
            if isinstance(backup_path, str):
                restore_backup(source_root, task.id, Path(backup_path))
            raise
        plan = ExportPlan(files=(), datasets=(), latent_records=(), input_digest=input_digest)
        if status != TaskStatus.EXPORTING.value:
            return self._summary(task.id, plan, status)
        if not finalize_phase:
            return self._summary(
                task.id,
                plan,
                TaskStatus.EXPORTING.value,
                component_complete=True,
            )
        status = self._complete_or_control(
            token,
            task.config_hash,
            batch_index + 1,
            len(paths),
            cursor,
        )
        return self._summary(task.id, plan, status, component_complete=True)

    def _complete_or_control(
        self,
        token: WorkerToken,
        config_hash: str,
        batch_index: int,
        completed: int,
        cursor: dict,
    ) -> str:
        if self._control_requested(token.task_id):
            return self._commit_control(
                token, config_hash, batch_index, completed, cursor
            )
        try:
            return self.tasks.complete_phase(token, phase=TaskStatus.EXPORTING).status
        except StaleWorkerToken:
            return self.tasks.get_task(token.task_id).status
        except InvalidTaskTransition:
            current = self.tasks.get_task(token.task_id)
            if current.status not in {
                TaskStatus.PAUSING.value,
                TaskStatus.TERMINATING.value,
            }:
                raise
            return self._commit_control(token, config_hash, batch_index, completed, cursor)

    def _commit_control(
        self,
        token: WorkerToken,
        config_hash: str,
        batch_index: int,
        completed: int,
        cursor: dict,
    ) -> str:
        return self.tasks.commit_batch(
            token,
            phase=TaskStatus.EXPORTING,
            config_hash=config_hash,
            batch_index=batch_index,
            completed_items=completed,
            progress_total=completed,
            cursor={**cursor, "control_only": True},
            lease_seconds=300,
        ).task.status

    def _control_requested(self, task_id: str) -> bool:
        return self.tasks.get_task(task_id).status in {
            TaskStatus.PAUSING.value,
            TaskStatus.TERMINATING.value,
        }

    @staticmethod
    def _summary(
        task_id: str,
        plan: ExportPlan,
        status: str,
        *,
        component_complete: bool = False,
    ) -> ExportSummary:
        return ExportSummary(
            task_id=task_id,
            datasets=len(plan.datasets),
            files=len(plan.files),
            bytes=sum(file.size_bytes for file in plan.files),
            resumed_from_file=0,
            component_complete=component_complete,
            artifact_cache_key=None,
            final_status=status,
        )
