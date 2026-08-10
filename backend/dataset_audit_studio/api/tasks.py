from __future__ import annotations

import asyncio
import math
import shutil
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from dataset_audit_studio.adapters.component_run_repository import (
    ComponentRunRepository,
)
from dataset_audit_studio.api.schemas import (
    CheckpointListResponse,
    CheckpointResponse,
    ExportRunCreate,
    ExportRunListResponse,
    ExportRunPreviewRequest,
    ExportRunPreviewResponse,
    ExportRunResponse,
    ReviewGateReleaseRequest,
    RewritePreviewConfirmationRequest,
    TaskConfigUpdate,
    TaskControlRequest,
    TaskCreate,
    TaskDeleteRequest,
    TaskDeleteResponse,
    TaskEventListResponse,
    TaskEventResponse,
    TaskListResponse,
    TaskResponse,
    TaskTerminateRequest,
    WatermarkReviewThresholdRequest,
    WatermarkReviewThresholdResponse,
)
from dataset_audit_studio.app.component_catalog import component_phase_map
from dataset_audit_studio.app.component_task_config import (
    ComponentTaskConfigMaterializer,
)
from dataset_audit_studio.clustering.config import ClusteringConfig
from dataset_audit_studio.core.profile_contracts import DatasetProfile
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.export.config import ExportConfig
from dataset_audit_studio.export.repository import ExportRepository
from dataset_audit_studio.export.rewrite import restore_latest_backup, rewrite_preview_digest
from dataset_audit_studio.export_runs.errors import ExportRunError
from dataset_audit_studio.export_runs.service import ExportRunService
from dataset_audit_studio.jobs.errors import LegacyTaskConfigUnsupported
from dataset_audit_studio.jobs.profile import has_builtin_profile
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.latent.config import LatentConfig
from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.discovery import (
    SourceLayoutError,
    validate_builtin_profile_input_layout,
)
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.style.config import StyleConfig

router = APIRouter(prefix="/tasks", tags=["tasks"])

LEGACY_TASK_CONFIG_ERROR_CODE = "legacy_task_config_unsupported"


def _legacy_task_config_response() -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": LEGACY_TASK_CONFIG_ERROR_CODE,
            "detail": "Profile-free task configuration is no longer supported",
        },
    )


def _has_component_payload(config: object) -> bool:
    return isinstance(config, dict) and isinstance(config.get("components"), dict)


def _service(request: Request) -> TaskService:
    return TaskService(request.app.state.database)


def _source_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise HTTPException(status_code=422, detail="source_root must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise HTTPException(
            status_code=422, detail=f"source_root does not exist: {path}"
        ) from error
    if not resolved.is_dir():
        raise HTTPException(status_code=422, detail="source_root must be a directory")
    return resolved


def _output_path(raw_path: str | None, source: Path) -> Path | None:
    if raw_path is None:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise HTTPException(status_code=422, detail="output_root must be an absolute path")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(source)
    except ValueError:
        return resolved
    raise HTTPException(status_code=422, detail="output_root cannot be inside source_root")


def _task_response(task) -> TaskResponse:
    return TaskResponse.from_view(task)


def _require_profile_task(task):
    if not has_builtin_profile(task.config):
        raise HTTPException(
            status_code=422,
            detail=LEGACY_TASK_CONFIG_ERROR_CODE,
        )
    return task


def _wake_worker(request: Request) -> None:
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.wake()


def _export_run_error_response(error: ExportRunError) -> JSONResponse:
    status_by_code = {
        "task_not_found": 404,
        "legacy_task_config_unsupported": 422,
        "export_output_path_invalid": 422,
        "export_aesthetic_minimum_invalid": 422,
        "export_domain_minimum_invalid": 422,
        "export_duplicate_filter_invalid": 422,
        "export_style_outlier_mode_invalid": 422,
        "export_task_not_completed": 409,
        "export_output_not_empty": 409,
        "export_output_already_used": 409,
        "export_resolution_unavailable": 409,
        "export_preview_required": 422,
        "export_preview_stale": 409,
        "export_empty_output": 409,
        "export_duplicate_analysis_incomplete": 409,
        "export_duplicate_evidence_invalid": 409,
        "export_duplicate_group_fully_excluded": 409,
        "export_task_version_conflict": 409,
        "export_review_not_ready": 409,
        "export_first_run_exists": 409,
        "export_mode_unsupported": 409,
        "export_legacy_payload_unsupported": 422,
        "export_minimum_folder_images_invalid": 422,
        "export_add_repeat_prefix_invalid": 422,
        "export_sample_seen_mode_invalid": 422,
        "export_sample_seen_target_invalid": 422,
        "export_repeat_prefix_required": 422,
        "export_collision": 409,
    }
    return JSONResponse(
        status_code=status_by_code.get(error.code, 409),
        content={"code": error.code, "detail": str(error)},
    )


def _task_cache_path(request: Request, task_id: str) -> Path:
    try:
        normalized_id = str(UUID(task_id))
    except ValueError as error:
        raise ValueError("Task id is not a UUID") from error
    if normalized_id != task_id.lower():
        raise ValueError("Task id is not normalized")

    project_root = Path(request.app.state.project_root).resolve(strict=False)
    cache_root = (project_root / "data" / "tasks").resolve(strict=False)
    try:
        cache_root.relative_to(project_root)
    except ValueError as error:
        raise ValueError("Task cache root escapes the project") from error
    path = cache_root / normalized_id
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(cache_root)
    except ValueError as error:
        raise ValueError("Task cache path escapes project data") from error
    return path


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _clear_task_cache(request: Request, task_id: str) -> str | None:
    try:
        path = _task_cache_path(request, task_id)
        if _is_reparse_point(path):
            raise ValueError("Task cache directory is a symlink or reparse point")
        elif path.exists():
            shutil.rmtree(path)
    except (OSError, ValueError) as error:
        return str(error)
    return None


def _sync_component_runs(request: Request, task) -> None:
    registry = request.app.state.component_registry
    resolved = registry.resolve_task_config(task.config)
    with request.app.state.database.write_session() as session:
        ComponentRunRepository().sync_plan(
            session,
            task=task,
            resolved=resolved,
            phase_by_component=component_phase_map(registry),
        )


def _materializer(request: Request) -> ComponentTaskConfigMaterializer:
    return ComponentTaskConfigMaterializer(request.app.state.component_registry)


def _validate_scan_config(config: dict) -> None:
    ScanConfig.from_task_config(config)
    ScoringConfig.from_task_config(config)
    style = StyleConfig.from_task_config(config)
    clustering = ClusteringConfig.from_task_config(config)
    LatentConfig.from_task_config(config)
    ExportConfig.from_task_config(config)
    components = config.get("components")
    if not isinstance(components, dict):
        raise ValueError("legacy_task_config_unsupported: complete components are required")
    aesthetic = components.get("score.aesthetic_domain")
    export = components.get("export.dataset")
    if not isinstance(aesthetic, dict) or not isinstance(export, dict):
        raise ValueError("Complete component configuration is required")
    aesthetic_config = aesthetic.get("config")
    export_config = export.get("config")
    if not isinstance(aesthetic_config, dict) or not isinstance(export_config, dict):
        raise ValueError("Complete component configuration is required")
    minimum = export_config.get("aesthetic_minimum")
    if minimum is not None:
        if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
            raise ValueError("Aesthetic minimum must be numeric")
        if not math.isfinite(float(minimum)) or not 1.0 <= float(minimum) <= 5.0:
            raise ValueError("Aesthetic minimum must be finite and between 1 and 5")
        if not aesthetic.get("enabled"):
            raise ValueError("Aesthetic curation requires enabled aesthetic scoring")
    export_mode = export_config.get("mode")
    if export_mode == "rewrite" and export_config.get("backup_enabled") is not True:
        raise ValueError("Rewrite mode requires backup_enabled=true")
    if (
        clustering.enabled
        and clustering.scope_mode == "artist"
        and not style.enabled
        and config.get("profile") != DatasetProfile.CHARACTER_CONCEPT.value
    ):
        raise ValueError("Artist clustering requires style analysis to be enabled")


def _validate_profile_input_layout(request: Request, source: Path, config: dict) -> None:
    profile = _materializer(request).profile_from_task_config(config)
    if profile is None:
        return
    validate_builtin_profile_input_layout(
        source,
        ScanConfig.from_task_config(config),
        project_root=Path(request.app.state.project_root),
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, request: Request) -> TaskResponse:
    if payload.components is None and not _has_component_payload(payload.config):
        return _legacy_task_config_response()  # type: ignore[return-value]
    source = _source_path(payload.source_root)
    output = _output_path(payload.output_root, source)
    try:
        if payload.components is not None:
            if payload.config:
                raise ValueError("config and components cannot be supplied together")
            config = _materializer(request).materialize(
                payload.components,
                profile=payload.profile,
                require_profile=True,
            )
        else:
            config = _materializer(request).materialize_task_config(
                payload.config,
                require_profile=True,
            )
        _validate_scan_config(config)
        _validate_profile_input_layout(request, source, config)
        task = _service(request).create_task(
            name=payload.name,
            source_root=str(source),
            output_root=str(output) if output is not None else None,
            config=config,
        )
        _sync_component_runs(request, task)
    except LegacyTaskConfigUnsupported:
        return _legacy_task_config_response()  # type: ignore[return-value]
    except SourceLayoutError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"Invalid task config: {error}") from error
    return _task_response(task)


@router.get("", response_model=TaskListResponse)
def list_tasks(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
) -> TaskListResponse:
    tasks, total = _service(request).list_tasks(offset=offset, limit=limit, status=task_status)
    return TaskListResponse(
        items=[_task_response(task) for task in tasks],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/{task_id}/export-runs",
    response_model=ExportRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_export_run(
    task_id: str,
    payload: ExportRunCreate,
    request: Request,
) -> ExportRunResponse | JSONResponse:
    try:
        run = ExportRunService(request.app.state.database).create(
            task_id,
            output_root=payload.output_root,
            minimum_resolution=payload.minimum_resolution,
            domain_minimum=payload.domain_minimum,
            exclude_exact_visual_duplicates=payload.exclude_exact_visual_duplicates
            if payload.exclude_exact_visual_duplicates is not None
            else False,
            style_outlier_mode=payload.style_outlier_mode
            if payload.style_outlier_mode is not None
            else "off",
            aesthetic_minimum=payload.aesthetic_minimum,
            minimum_folder_images=payload.minimum_folder_images
            if payload.minimum_folder_images is not None
            else 1,
            add_repeat_prefix=payload.add_repeat_prefix
            if payload.add_repeat_prefix is not None
            else True,
            sample_seen_mode=payload.sample_seen_mode
            if payload.sample_seen_mode is not None
            else "off",
            sample_seen_target=payload.sample_seen_target,
            preview_digest=payload.preview_digest,
        )
    except ExportRunError as error:
        return _export_run_error_response(error)
    _wake_worker(request)
    return ExportRunResponse.from_view(run)


@router.post(
    "/{task_id}/export-runs/preview",
    response_model=ExportRunPreviewResponse,
)
def preview_export_run(
    task_id: str,
    payload: ExportRunPreviewRequest,
    request: Request,
) -> ExportRunPreviewResponse | JSONResponse:
    try:
        preview = ExportRunService(request.app.state.database).preview(
            task_id,
            output_root=payload.output_root,
            minimum_resolution=payload.minimum_resolution,
            domain_minimum=payload.domain_minimum,
            exclude_exact_visual_duplicates=payload.exclude_exact_visual_duplicates
            if payload.exclude_exact_visual_duplicates is not None
            else False,
            style_outlier_mode=payload.style_outlier_mode
            if payload.style_outlier_mode is not None
            else "off",
            aesthetic_minimum=payload.aesthetic_minimum,
            minimum_folder_images=payload.minimum_folder_images
            if payload.minimum_folder_images is not None
            else 1,
            add_repeat_prefix=payload.add_repeat_prefix
            if payload.add_repeat_prefix is not None
            else True,
            sample_seen_mode=payload.sample_seen_mode
            if payload.sample_seen_mode is not None
            else "off",
            sample_seen_target=payload.sample_seen_target,
        )
    except ExportRunError as error:
        return _export_run_error_response(error)
    return ExportRunPreviewResponse.from_preview(preview)


@router.get("/{task_id}/export-runs", response_model=ExportRunListResponse)
def list_export_runs(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ExportRunListResponse | JSONResponse:
    try:
        runs, total = ExportRunService(request.app.state.database).list_for_task(
            task_id, offset=offset, limit=limit
        )
    except ExportRunError as error:
        return _export_run_error_response(error)
    return ExportRunListResponse(
        items=[ExportRunResponse.from_view(run) for run in runs],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, request: Request) -> TaskResponse:
    return _task_response(_require_profile_task(_service(request).get_task(task_id)))


@router.put("/{task_id}/config", response_model=TaskResponse)
def update_task_config(task_id: str, payload: TaskConfigUpdate, request: Request) -> TaskResponse:
    if not _has_component_payload(payload.config):
        return _legacy_task_config_response()  # type: ignore[return-value]
    try:
        service = _service(request)
        existing = service.get_task(task_id)
        materializer = _materializer(request)
        if materializer.profile_from_task_config(existing.config) is None:
            return _legacy_task_config_response()  # type: ignore[return-value]
        config = materializer.materialize_task_config(
            payload.config,
            profile=materializer.profile_from_task_config(existing.config),
        )
        _validate_scan_config(config)
        task = service.update_config(
            task_id,
            config,
            expected_version=payload.expected_version,
        )
        _sync_component_runs(request, task)
    except LegacyTaskConfigUnsupported:
        return _legacy_task_config_response()  # type: ignore[return-value]
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"Invalid task config: {error}") from error
    return _task_response(task)


@router.post("/{task_id}/queue", response_model=TaskResponse)
def queue_task(task_id: str, payload: TaskControlRequest, request: Request) -> TaskResponse:
    task = _service(request).queue_task(task_id, expected_version=payload.expected_version)
    _wake_worker(request)
    return _task_response(task)


@router.post("/{task_id}/pause", response_model=TaskResponse)
def pause_task(task_id: str, payload: TaskControlRequest, request: Request) -> TaskResponse:
    return _task_response(
        _service(request).request_pause(task_id, expected_version=payload.expected_version)
    )


@router.post("/{task_id}/resume", response_model=TaskResponse)
def resume_task(task_id: str, payload: TaskControlRequest, request: Request) -> TaskResponse:
    task = _service(request).resume_task(task_id, expected_version=payload.expected_version)
    _wake_worker(request)
    return _task_response(task)


@router.post("/{task_id}/terminate", response_model=TaskResponse)
def terminate_task(task_id: str, payload: TaskTerminateRequest, request: Request) -> TaskResponse:
    return _task_response(
        _service(request).request_terminate(
            task_id,
            force=payload.force,
            reason=payload.reason,
            expected_version=payload.expected_version,
        )
    )


@router.delete("/{task_id}", response_model=TaskDeleteResponse)
def delete_task(
    task_id: str,
    payload: TaskDeleteRequest,
    request: Request,
) -> TaskDeleteResponse:
    deleted = _service(request).delete_task(
        task_id,
        expected_version=payload.expected_version,
    )
    cache_error = _clear_task_cache(request, deleted.id)
    return TaskDeleteResponse(
        task_id=deleted.id,
        cache_cleared=cache_error is None,
        cache_cleanup_error=cache_error,
    )


@router.post(
    "/{task_id}/review-gate/release",
    response_model=TaskResponse | ExportRunResponse,
)
def release_review_gate(
    task_id: str, payload: ReviewGateReleaseRequest, request: Request
) -> TaskResponse | ExportRunResponse | JSONResponse:
    task = _service(request).get_task(task_id)
    if ExportConfig.from_task_config(task.config).mode == "copy":
        if payload.expected_gate != TaskStatus.EVIDENCE_REVIEW.value:
            raise HTTPException(status_code=409, detail="Expected review gate evidence_review")
        try:
            run = ExportRunService(request.app.state.database).complete_first_copy_export(
                task_id,
                output_root=payload.output_root,
                minimum_resolution=payload.minimum_resolution,
                domain_minimum=payload.domain_minimum,
                exclude_exact_visual_duplicates=payload.exclude_exact_visual_duplicates
                if payload.exclude_exact_visual_duplicates is not None
                else False,
                style_outlier_mode=payload.style_outlier_mode
                if payload.style_outlier_mode is not None
                else "off",
                aesthetic_minimum=payload.aesthetic_minimum,
                minimum_folder_images=payload.minimum_folder_images
                if payload.minimum_folder_images is not None
                else 1,
                add_repeat_prefix=payload.add_repeat_prefix
                if payload.add_repeat_prefix is not None
                else True,
                sample_seen_mode=payload.sample_seen_mode
                if payload.sample_seen_mode is not None
                else "off",
                sample_seen_target=payload.sample_seen_target,
                preview_digest=payload.preview_digest,
                expected_version=payload.expected_version,
            )
        except ExportRunError as error:
            return _export_run_error_response(error)
        _wake_worker(request)
        return ExportRunResponse.from_view(run)
    return _task_response(
        _service(request).release_review_gate(
            task_id,
            expected_gate=payload.expected_gate,
            expected_version=payload.expected_version,
        )
    )


@router.post("/{task_id}/rewrite-preview")
def preview_rewrite(task_id: str, request: Request) -> dict[str, object]:
    service = _service(request)
    task = service.get_task(task_id)
    export_config = ExportConfig.from_task_config(task.config)
    if export_config.mode != "rewrite":
        raise HTTPException(status_code=422, detail="Rewrite preview requires rewrite mode")
    source_root = Path(task.source_root).resolve(strict=True)
    repository = ExportRepository(project_root=Path(request.app.state.project_root))
    with request.app.state.database.read_session() as session:
        workspace = repository.load_curated(session, task)
        retained = {sample.sample_id for sample in workspace.samples}
        paths = repository.rewrite_paths(
            session,
            task,
            retained,
            keep_latent=export_config.keep_latent_files,
            keep_annotation=export_config.keep_annotation_files,
        )
    digest = rewrite_preview_digest(
        task_id=task.id,
        config_hash=task.config_hash,
        config_revision=task.current_config_revision,
        curated_sample_ids=tuple(sample.sample_id for sample in workspace.samples),
        paths=paths,
        source_root=source_root,
    )
    return {
        "preview_digest": digest,
        "config_hash": task.config_hash,
        "config_revision": task.current_config_revision,
        "curated_sample_count": len(workspace.samples),
        "rewrite_file_count": len(paths),
        "relative_paths": [path.relative_to(source_root).as_posix() for path in paths],
    }


@router.post("/{task_id}/rewrite-preview/confirm", response_model=TaskResponse)
def confirm_rewrite_preview(
    task_id: str,
    payload: RewritePreviewConfirmationRequest,
    request: Request,
) -> TaskResponse:
    try:
        task = _service(request).confirm_rewrite_preview(
            task_id,
            preview_digest=payload.preview_digest,
            expected_version=payload.expected_version,
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _task_response(task)


@router.post("/{task_id}/rewrite-backup/restore")
def restore_rewrite_backup(
    task_id: str,
    payload: TaskControlRequest,
    request: Request,
) -> dict[str, object]:
    task = _service(request).get_task(task_id)
    if payload.expected_version is not None and payload.expected_version != task.row_version:
        raise HTTPException(status_code=409, detail="Task version changed before restore")
    try:
        return restore_latest_backup(Path(task.source_root), task.id)
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/{task_id}/watermark-review-threshold",
    response_model=WatermarkReviewThresholdResponse,
)
def reclassify_watermark_review_threshold(
    task_id: str,
    payload: WatermarkReviewThresholdRequest,
    request: Request,
) -> WatermarkReviewThresholdResponse:
    result = _service(request).reclassify_watermark_evidence(
        task_id,
        threshold=payload.threshold,
        expected_version=payload.expected_version,
    )
    return WatermarkReviewThresholdResponse(
        threshold=result.threshold,
        updated=result.updated,
        candidates=result.candidates,
    )


@router.get("/{task_id}/events", response_model=TaskEventListResponse)
def list_task_events(
    task_id: str,
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> TaskEventListResponse:
    service = _service(request)
    events = service.list_events(task_id, after=after, limit=limit)
    return TaskEventListResponse(
        items=[TaskEventResponse.from_view(event) for event in events],
        next_after=events[-1].sequence if events else after,
        latest_sequence=service.latest_event_sequence(task_id),
    )


@router.get("/{task_id}/checkpoints", response_model=CheckpointListResponse)
def list_task_checkpoints(
    task_id: str, request: Request, phase: str | None = None
) -> CheckpointListResponse:
    checkpoints = _service(request).list_checkpoints(task_id, phase=phase)
    return CheckpointListResponse(
        items=[CheckpointResponse.from_view(item) for item in checkpoints]
    )


@router.get("/{task_id}/events/stream")
async def stream_task_events(
    task_id: str,
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    service = _service(request)
    await run_in_threadpool(service.get_task, task_id)
    if last_event_id is not None:
        try:
            after = max(after, int(last_event_id))
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="Last-Event-ID must be an integer"
            ) from error

    async def event_stream() -> AsyncIterator[str]:
        cursor = after
        idle_ticks = 0
        while not await request.is_disconnected():
            events = await run_in_threadpool(service.list_events, task_id, after=cursor, limit=200)
            if events:
                idle_ticks = 0
                for event in events:
                    cursor = event.sequence
                    data = TaskEventResponse.from_view(event).model_dump_json()
                    yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
            else:
                idle_ticks += 1
                if idle_ticks >= 15:
                    idle_ticks = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
