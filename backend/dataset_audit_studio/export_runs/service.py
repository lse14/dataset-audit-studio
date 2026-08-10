from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dataset_audit_studio.core.file_integrity import is_reparse
from dataset_audit_studio.core.profile_contracts import DatasetProfile
from dataset_audit_studio.database.base import utc_now
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import ExportRun, Task, TaskConfig
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.export.tree_publisher import ExportTreePublisher
from dataset_audit_studio.export_runs.errors import ExportRunError, ExportRunNotFound
from dataset_audit_studio.export_runs.planner import ExportRunPlanner
from dataset_audit_studio.export_runs.types import ExportRunPreview, ExportRunView
from dataset_audit_studio.scoring.assets import EVIDENCE_SOURCES, PREPROCESSING_VERSIONS
from dataset_audit_studio.scoring.config import ScoringConfig

_PROFILE_VALUES = frozenset(profile.value for profile in DatasetProfile)
_SUPPORTED_RESOLUTIONS = frozenset((512, 768, 1024, 1216, 1536))


class ExportRunService:
    """Create and read independent copy-only export runs.

    This service deliberately owns no Task state transitions, events, or legacy
    Export rows. Execution is claimed by the shared worker slot separately.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        task_id: str,
        *,
        output_root: str,
        minimum_resolution: int,
        domain_minimum: float | None = None,
        exclude_exact_visual_duplicates: bool = False,
        style_outlier_mode: str = "off",
        aesthetic_minimum: float | None = None,
        minimum_folder_images: int = 1,
        add_repeat_prefix: bool = True,
        sample_seen_mode: str = "off",
        sample_seen_target: int | None = None,
        preview_digest: str | None = None,
    ) -> ExportRunView:
        return self._create(
            task_id,
            output_root=output_root,
            minimum_resolution=minimum_resolution,
            domain_minimum=domain_minimum,
            exclude_exact_visual_duplicates=exclude_exact_visual_duplicates,
            style_outlier_mode=style_outlier_mode,
            aesthetic_minimum=aesthetic_minimum,
            minimum_folder_images=minimum_folder_images,
            add_repeat_prefix=add_repeat_prefix,
            sample_seen_mode=sample_seen_mode,
            sample_seen_target=sample_seen_target,
            preview_digest=preview_digest,
            expected_status=TaskStatus.COMPLETED,
            expected_version=None,
            complete_task=False,
        )

    def complete_first_copy_export(
        self,
        task_id: str,
        *,
        output_root: str,
        minimum_resolution: int,
        domain_minimum: float | None = None,
        exclude_exact_visual_duplicates: bool = False,
        style_outlier_mode: str = "off",
        aesthetic_minimum: float | None = None,
        minimum_folder_images: int = 1,
        add_repeat_prefix: bool = True,
        sample_seen_mode: str = "off",
        sample_seen_target: int | None = None,
        preview_digest: str | None = None,
        expected_version: int | None = None,
    ) -> ExportRunView:
        return self._create(
            task_id,
            output_root=output_root,
            minimum_resolution=minimum_resolution,
            domain_minimum=domain_minimum,
            exclude_exact_visual_duplicates=exclude_exact_visual_duplicates,
            style_outlier_mode=style_outlier_mode,
            aesthetic_minimum=aesthetic_minimum,
            minimum_folder_images=minimum_folder_images,
            add_repeat_prefix=add_repeat_prefix,
            sample_seen_mode=sample_seen_mode,
            sample_seen_target=sample_seen_target,
            preview_digest=preview_digest,
            expected_status=TaskStatus.EVIDENCE_REVIEW,
            expected_version=expected_version,
            complete_task=True,
        )

    def _create(
        self,
        task_id: str,
        *,
        output_root: str,
        minimum_resolution: int,
        domain_minimum: float | None,
        exclude_exact_visual_duplicates: bool,
        style_outlier_mode: str,
        aesthetic_minimum: float | None,
        minimum_folder_images: int,
        add_repeat_prefix: bool,
        sample_seen_mode: str,
        sample_seen_target: int | None,
        preview_digest: str | None,
        expected_status: TaskStatus,
        expected_version: int | None,
        complete_task: bool,
    ) -> ExportRunView:
        path = self._validate_output_root(output_root)
        self._validate_resolution(minimum_resolution)
        domain = self._validate_domain_minimum(domain_minimum)
        duplicate_filter = self._validate_duplicate_filter(exclude_exact_visual_duplicates)
        style_mode = self._validate_style_outlier_mode(style_outlier_mode)
        threshold = self._validate_aesthetic_minimum(aesthetic_minimum)
        selection = self._validate_selection_settings(
            minimum_folder_images=minimum_folder_images,
            add_repeat_prefix=add_repeat_prefix,
            sample_seen_mode=sample_seen_mode,
            sample_seen_target=sample_seen_target,
        )
        if not self._is_digest(preview_digest):
            raise ExportRunError("export_preview_required", "A current preview_digest is required")
        output_key = self._output_key(path)
        try:
            with self.database.write_session() as session:
                task, config = self._current_task_config(session, task_id)
                if not self._has_builtin_profile(config.config_json):
                    raise ExportRunError(
                        "legacy_task_config_unsupported",
                        "Profile-free task configuration is no longer supported",
                    )
                if expected_version is not None and task.row_version != expected_version:
                    raise ExportRunError(
                        "export_task_version_conflict",
                        "Task changed before first export confirmation",
                    )
                if task.status != expected_status.value:
                    raise ExportRunError(
                        "export_review_not_ready" if complete_task else "export_task_not_completed",
                        "Final review is no longer available"
                        if complete_task
                        else "Only completed tasks can create an export run",
                    )
                if complete_task and not self._is_copy_config(config.config_json):
                    raise ExportRunError(
                        "export_mode_unsupported",
                        "First export review release requires copy mode",
                    )
                if complete_task and session.scalar(
                    select(ExportRun.id).where(ExportRun.task_id == task.id)
                ) is not None:
                    raise ExportRunError(
                        "export_first_run_exists",
                        "The first export run has already been created",
                    )
                try:
                    ExportTreePublisher().validate_roots(
                        Path(task.source_root).resolve(strict=False),
                        path,
                    )
                except ValueError as error:
                    raise ExportRunError("export_output_path_invalid", str(error)) from error
                if (
                    session.scalar(select(ExportRun.id).where(ExportRun.output_key == output_key))
                    is not None
                ):
                    raise ExportRunError(
                        "export_output_already_used",
                        "Export output has already been used by an export run",
                    )
                if any(path.iterdir()):
                    raise ExportRunError(
                        "export_output_not_empty", "Export output directory is not empty"
                    )
                planner = ExportRunPlanner(self.database)
                settings = self._copy_settings(
                    config.config_json,
                    minimum_folder_images=selection["minimum_folder_images"],
                    add_repeat_prefix=selection["add_repeat_prefix"],
                    sample_seen_mode=selection["sample_seen_mode"],
                    sample_seen_target=selection["sample_seen_target"],
                    minimum_resolution=minimum_resolution,
                    domain_minimum=domain,
                    exclude_exact_visual_duplicates=duplicate_filter,
                    style_outlier_mode=style_mode,
                    aesthetic_minimum=threshold,
                )
                planned = planner._build_current(session, task, config, settings)
                if planned.preview_digest != preview_digest:
                    raise ExportRunError(
                        "export_preview_stale",
                        "Export preview is stale; refresh before creating the run",
                    )
                if int(planned.summary.get("included_count", 0)) == 0:
                    raise ExportRunError(
                        "export_empty_output", "Export preview contains no eligible samples"
                    )
                identity = (
                    self._aesthetic_identity(config.config_json) if threshold is not None else None
                )
                run = ExportRun(
                    task_id=task.id,
                    task_config_revision=task.current_config_revision,
                    config_hash=config.config_hash,
                    selection_version=task.current_config_revision,
                    output_root=str(path),
                    output_key=output_key,
                    minimum_resolution=minimum_resolution,
                    resolutions_json=[minimum_resolution],
                    aesthetic_minimum=threshold,
                    minimum_folder_images=selection["minimum_folder_images"],
                    add_repeat_prefix=selection["add_repeat_prefix"],
                    sample_seen_mode=selection["sample_seen_mode"],
                    sample_seen_target=selection["sample_seen_target"],
                    preview_digest=preview_digest,
                    settings_json=settings,
                    aesthetic_identity_json=identity,
                    input_digest=planned.plan.input_digest,
                    input_snapshot_json=planned.input_snapshot,
                )
                session.add(run)
                if complete_task:
                    task.status = TaskStatus.COMPLETED.value
                    task.resume_state = None
                    task.error_code = None
                    task.error_message = None
                    task.finished_at = utc_now()
                    task.row_version += 1
                session.flush()
                return self._view(run)
        except IntegrityError as error:
            if "output_key" in str(error).lower():
                raise ExportRunError(
                    "export_output_already_used",
                    "Export output has already been used by an export run",
                ) from error
            raise

    def preview(
        self,
        task_id: str,
        *,
        output_root: str,
        minimum_resolution: int,
        domain_minimum: float | None = None,
        exclude_exact_visual_duplicates: bool = False,
        style_outlier_mode: str = "off",
        aesthetic_minimum: float | None = None,
        minimum_folder_images: int = 1,
        add_repeat_prefix: bool = True,
        sample_seen_mode: str = "off",
        sample_seen_target: int | None = None,
    ) -> ExportRunPreview:
        path = self._validate_output_root(output_root)
        self._validate_resolution(minimum_resolution)
        domain = self._validate_domain_minimum(domain_minimum)
        duplicate_filter = self._validate_duplicate_filter(exclude_exact_visual_duplicates)
        style_mode = self._validate_style_outlier_mode(style_outlier_mode)
        threshold = self._validate_aesthetic_minimum(aesthetic_minimum)
        selection = self._validate_selection_settings(
            minimum_folder_images=minimum_folder_images,
            add_repeat_prefix=add_repeat_prefix,
            sample_seen_mode=sample_seen_mode,
            sample_seen_target=sample_seen_target,
        )
        with self.database.read_session() as session:
            task, config = self._current_task_config(session, task_id)
            if not self._has_builtin_profile(config.config_json):
                raise ExportRunError(
                    "legacy_task_config_unsupported",
                    "Profile-free task configuration is no longer supported",
                )
            if task.status not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.EVIDENCE_REVIEW.value,
            }:
                raise ExportRunError(
                    "export_task_not_completed", "Only completed tasks can preview an export run"
                )
            try:
                ExportTreePublisher().validate_roots(
                    Path(task.source_root).resolve(strict=False), path
                )
            except ValueError as error:
                raise ExportRunError("export_output_path_invalid", str(error)) from error
            if any(path.iterdir()):
                raise ExportRunError(
                    "export_output_not_empty", "Export output directory is not empty"
                )
        preview = ExportRunPlanner(self.database).preview(
            task_id,
            minimum_resolution=minimum_resolution,
            domain_minimum=domain,
            exclude_exact_visual_duplicates=duplicate_filter,
            style_outlier_mode=style_mode,
            aesthetic_minimum=threshold,
            minimum_folder_images=selection["minimum_folder_images"],
            add_repeat_prefix=selection["add_repeat_prefix"],
            sample_seen_mode=selection["sample_seen_mode"],
            sample_seen_target=selection["sample_seen_target"],
        )
        return preview

    def get(self, run_id: str) -> ExportRunView:
        with self.database.read_session() as session:
            run = session.get(ExportRun, run_id)
            if run is None:
                raise ExportRunNotFound(run_id)
            return self._view(run)

    def list_for_task(
        self,
        task_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[ExportRunView, ...], int]:
        with self.database.read_session() as session:
            task, config = self._current_task_config(session, task_id)
            if not self._has_builtin_profile(config.config_json):
                raise ExportRunError(
                    "legacy_task_config_unsupported",
                    "Profile-free task configuration is no longer supported",
                )
            total = int(
                session.scalar(
                    select(func.count()).select_from(ExportRun).where(ExportRun.task_id == task.id)
                )
                or 0
            )
            runs = session.scalars(
                select(ExportRun)
                .where(ExportRun.task_id == task.id)
                .order_by(ExportRun.created_at.desc(), ExportRun.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(self._view(run) for run in runs), total

    @staticmethod
    def _validate_output_root(raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ExportRunError("export_output_path_invalid", "Export output path is required")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise ExportRunError(
                "export_output_path_invalid", "Export output path must be absolute"
            )
        try:
            if is_reparse(path):
                raise ExportRunError(
                    "export_output_path_invalid",
                    "Export output path must be a normal directory",
                )
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ExportRunError(
                "export_output_path_invalid", "Export output path must already exist"
            ) from error
        if not resolved.is_dir() or is_reparse(resolved):
            raise ExportRunError(
                "export_output_path_invalid", "Export output path must be a normal directory"
            )
        return resolved

    @staticmethod
    def _output_key(path: Path) -> str:
        return os.path.normcase(str(path)).casefold()

    @staticmethod
    def _validate_aesthetic_minimum(value: float | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExportRunError(
                "export_aesthetic_minimum_invalid", "Aesthetic minimum must be numeric"
            )
        normalized = float(value)
        if (
            not math.isfinite(normalized)
            or not 1.0 <= normalized <= 5.0
            or normalized * 2 != round(normalized * 2)
        ):
            raise ExportRunError(
                "export_aesthetic_minimum_invalid",
                "Aesthetic minimum must be a finite 0.5 step between 1.0 and 5.0",
            )
        return normalized

    @staticmethod
    def _validate_domain_minimum(value: float | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExportRunError("export_domain_minimum_invalid", "Domain minimum must be numeric")
        normalized = float(value)
        if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
            raise ExportRunError(
                "export_domain_minimum_invalid",
                "Domain minimum must be finite and between 0 and 1",
            )
        return normalized

    @staticmethod
    def _validate_duplicate_filter(value: bool) -> bool:
        if not isinstance(value, bool):
            raise ExportRunError(
                "export_duplicate_filter_invalid", "Duplicate filter must be boolean"
            )
        return value

    @staticmethod
    def _validate_style_outlier_mode(value: str) -> str:
        if value not in {"off", "strong", "all"}:
            raise ExportRunError(
                "export_style_outlier_mode_invalid",
                "Style outlier mode must be off, strong, or all",
            )
        return value

    @staticmethod
    def _validate_resolution(value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ExportRunError("export_resolution_unavailable", "Resolution is invalid")
        if value not in _SUPPORTED_RESOLUTIONS:
            raise ExportRunError(
                "export_resolution_unavailable", "Resolution is not a supported export tier"
            )

    @staticmethod
    def _validate_selection_settings(
        *,
        minimum_folder_images: int,
        add_repeat_prefix: bool,
        sample_seen_mode: str,
        sample_seen_target: int | None,
    ) -> dict[str, Any]:
        if (
            isinstance(minimum_folder_images, bool)
            or not isinstance(minimum_folder_images, int)
            or minimum_folder_images <= 0
        ):
            raise ExportRunError(
                "export_minimum_folder_images_invalid",
                "minimum_folder_images must be a positive integer",
            )
        if not isinstance(add_repeat_prefix, bool):
            raise ExportRunError(
                "export_add_repeat_prefix_invalid", "add_repeat_prefix must be boolean"
            )
        if sample_seen_mode not in {"off", "auto", "manual"}:
            raise ExportRunError(
                "export_sample_seen_mode_invalid", "sample_seen_mode must be off, auto, or manual"
            )
        if sample_seen_mode != "off" and not add_repeat_prefix:
            raise ExportRunError(
                "export_repeat_prefix_required", "sample-seen balancing requires add_repeat_prefix"
            )
        if sample_seen_mode == "manual":
            if (
                isinstance(sample_seen_target, bool)
                or not isinstance(sample_seen_target, int)
                or sample_seen_target <= 0
            ):
                raise ExportRunError(
                    "export_sample_seen_target_invalid",
                    "manual sample_seen_target must be a positive integer",
                )
        elif sample_seen_target is not None:
            raise ExportRunError(
                "export_sample_seen_target_invalid",
                "sample_seen_target is only valid in manual mode",
            )
        return {
            "minimum_folder_images": minimum_folder_images,
            "add_repeat_prefix": add_repeat_prefix,
            "sample_seen_mode": sample_seen_mode,
            "sample_seen_target": sample_seen_target,
        }

    @staticmethod
    def _is_digest(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    @staticmethod
    def _has_builtin_profile(config: object) -> bool:
        if not isinstance(config, dict):
            return False
        components = config.get("components")
        if not isinstance(components, dict):
            return False
        return config.get("profile") in _PROFILE_VALUES

    @staticmethod
    def _is_copy_config(config: object) -> bool:
        if not isinstance(config, dict):
            return False
        components = config.get("components")
        if not isinstance(components, dict):
            return False
        component = components.get("export.dataset")
        if not isinstance(component, dict):
            return False
        settings = component.get("config")
        return isinstance(settings, dict) and settings.get("mode") == "copy"

    @staticmethod
    def _copy_settings(
        config: dict[str, Any],
        *,
        minimum_folder_images: int,
        add_repeat_prefix: bool,
        sample_seen_mode: str,
        sample_seen_target: int | None,
        minimum_resolution: int,
        domain_minimum: float | None,
        exclude_exact_visual_duplicates: bool,
        style_outlier_mode: str,
        aesthetic_minimum: float | None,
    ) -> dict[str, Any]:
        export = config.get("export")
        if not isinstance(export, dict):
            export = {}
        return {
            "schema": "export.run.settings.v3",
            "mode": "copy",
            "keep_annotation_files": bool(export.get("keep_annotation_files", True)),
            "minimum_resolution": minimum_resolution,
            "domain_minimum": domain_minimum,
            "exclude_exact_visual_duplicates": exclude_exact_visual_duplicates,
            "style_outlier_mode": style_outlier_mode,
            "aesthetic_minimum": aesthetic_minimum,
            "minimum_folder_images": minimum_folder_images,
            "add_repeat_prefix": add_repeat_prefix,
            "sample_seen_mode": sample_seen_mode,
            "sample_seen_target": sample_seen_target,
        }

    @staticmethod
    def _aesthetic_identity(config: dict[str, Any]) -> dict[str, str]:
        scoring = ScoringConfig.from_task_config(config)
        return {
            "source": EVIDENCE_SOURCES["aesthetic"],
            "model_id": scoring.aesthetic.model_id,
            "config_hash": scoring.inference_config_hash("aesthetic"),
            "algorithm_version": PREPROCESSING_VERSIONS["aesthetic"],
        }

    @staticmethod
    def _current_task_config(session, task_id: str) -> tuple[Task, TaskConfig]:
        task = session.get(Task, task_id)
        if task is None:
            raise ExportRunError("task_not_found", f"Task not found: {task_id}")
        config = session.scalar(
            select(TaskConfig).where(
                TaskConfig.task_id == task.id,
                TaskConfig.revision == task.current_config_revision,
            )
        )
        if config is None:
            raise RuntimeError(f"Task {task.id} has no active config revision")
        return task, config

    @staticmethod
    def _view(run: ExportRun) -> ExportRunView:
        return ExportRunView(
            id=run.id,
            task_id=run.task_id,
            task_config_revision=run.task_config_revision,
            config_hash=run.config_hash,
            selection_version=run.selection_version,
            output_root=run.output_root,
            output_key=run.output_key,
            minimum_resolution=run.minimum_resolution,
            resolutions=tuple(run.resolutions_json),
            aesthetic_minimum=run.aesthetic_minimum,
            minimum_folder_images=run.minimum_folder_images,
            add_repeat_prefix=run.add_repeat_prefix,
            sample_seen_mode=run.sample_seen_mode,
            sample_seen_target=run.sample_seen_target,
            preview_digest=run.preview_digest,
            settings=dict(run.settings_json),
            aesthetic_identity=(
                dict(run.aesthetic_identity_json)
                if run.aesthetic_identity_json is not None
                else None
            ),
            status=run.status,
            checkpoint=dict(run.checkpoint_json),
            input_digest=run.input_digest,
            execution_epoch=run.execution_epoch,
            progress_current=run.progress_current,
            progress_total=run.progress_total,
            bytes_current=run.bytes_current,
            bytes_total=run.bytes_total,
            file_count=run.file_count,
            manifest_path=run.manifest_path,
            manifest_sha256=run.manifest_sha256,
            summary=dict(run.summary_json) if run.summary_json is not None else None,
            error_code=run.error_code,
            error_message=run.error_message,
            created_at=run.created_at,
            updated_at=run.updated_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
