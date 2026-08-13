from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from dataset_audit_studio.api.workspace_schemas import (
    ClusterListResponse,
    ClusterSampleListResponse,
    CoverageReportResponse,
    DirectoryListingResponse,
    DirectorySelectionRequest,
    DirectorySelectionResponse,
    FileSelectionRequest,
    FolderListResponse,
    ManualExclusionRequest,
    ManualExclusionResponse,
    RiskListResponse,
    RiskSampleDetailResponse,
    RiskSampleListResponse,
    TaskOverviewResponse,
)
from dataset_audit_studio.workspace.service import WorkspaceService
from dataset_audit_studio.workspace.windows_dialog import (
    DirectoryDialogBusy,
    DirectoryDialogError,
    DirectoryDialogUnavailable,
)
from dataset_audit_studio.workspace.windows_dialog import (
    select_directory as select_windows_directory,
)
from dataset_audit_studio.workspace.windows_dialog import (
    select_file as select_windows_file,
)

router = APIRouter(tags=["workspace"])


def _service(request: Request) -> WorkspaceService:
    return WorkspaceService(
        request.app.state.database,
        project_root=Path(request.app.state.project_root),
    )


@router.get("/tasks/{task_id}/overview", response_model=TaskOverviewResponse)
def task_overview(task_id: str, request: Request) -> TaskOverviewResponse:
    return TaskOverviewResponse.from_view(_service(request).overview(task_id))


@router.get("/tasks/{task_id}/coverage", response_model=CoverageReportResponse)
def task_coverage(
    task_id: str,
    request: Request,
    resolution: Annotated[int, Query(gt=0)],
) -> CoverageReportResponse:
    return CoverageReportResponse.from_view(
        _service(request).coverage(task_id, resolution=resolution)
    )


@router.get("/tasks/{task_id}/folders", response_model=FolderListResponse)
def task_folders(task_id: str, request: Request) -> FolderListResponse:
    return FolderListResponse.from_view(_service(request).folders(task_id))


@router.get("/tasks/{task_id}/clusters", response_model=ClusterListResponse)
def task_clusters(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
) -> ClusterListResponse:
    return ClusterListResponse.from_view(
        _service(request).clusters(
            task_id,
            offset=offset,
            limit=limit,
            folder=folder,
        )
    )


@router.get(
    "/tasks/{task_id}/clusters/{cluster_id}/samples",
    response_model=ClusterSampleListResponse,
)
def task_cluster_samples(
    task_id: str,
    cluster_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
) -> ClusterSampleListResponse:
    return ClusterSampleListResponse.from_view(
        _service(request).cluster_samples(
            task_id,
            cluster_id,
            offset=offset,
            limit=limit,
            folder=folder,
        )
    )


@router.get("/tasks/{task_id}/risks", response_model=RiskListResponse)
def task_risks(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    code: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
) -> RiskListResponse:
    return RiskListResponse.from_view(
        _service(request).risks(
            task_id,
            offset=offset,
            limit=limit,
            code=code,
        )
    )


@router.get("/tasks/{task_id}/risk-samples", response_model=RiskSampleListResponse)
def task_risk_samples(
    task_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    code: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    folder: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
    severity: Annotated[
        Literal["info", "low", "medium", "high", "fatal"] | None,
        Query(),
    ] = None,
    decision: Literal["all", "pending_review", "approved_keep", "approved_exclude"] = "all",
) -> RiskSampleListResponse:
    return RiskSampleListResponse.from_view(
        _service(request).risk_samples(
            task_id,
            offset=offset,
            limit=limit,
            code=code,
            folder=folder,
            severity=severity,
            decision=decision,
        )
    )


@router.get(
    "/tasks/{task_id}/risk-samples/{sample_id}",
    response_model=RiskSampleDetailResponse,
)
def task_risk_sample_detail(
    task_id: str,
    sample_id: str,
    request: Request,
    code: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    severity: Annotated[
        Literal["info", "low", "medium", "high", "fatal"] | None,
        Query(),
    ] = None,
) -> RiskSampleDetailResponse:
    return RiskSampleDetailResponse.from_view(
        _service(request).risk_sample_detail(
            task_id,
            sample_id,
            code=code,
            severity=severity,
        )
    )


@router.post(
    "/tasks/{task_id}/manual-exclusions",
    response_model=ManualExclusionResponse,
)
def task_manual_exclusions(
    task_id: str,
    payload: ManualExclusionRequest,
    request: Request,
) -> ManualExclusionResponse:
    result = _service(request).set_manual_exclusions(
        task_id,
        sample_ids=payload.sample_ids,
        excluded=payload.excluded,
        context=payload.context,
    )
    return ManualExclusionResponse.from_result(result)


@router.get("/tasks/{task_id}/samples/{sample_id}/thumbnail")
def sample_thumbnail(
    task_id: str,
    sample_id: str,
    request: Request,
    size: Annotated[int, Query(ge=96, le=768)] = 256,
) -> FileResponse:
    try:
        path = _service(request).thumbnail(task_id, sample_id, size=size)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/tasks/{task_id}/samples/{sample_id}/media")
def sample_media(task_id: str, sample_id: str, request: Request) -> FileResponse:
    media = _service(request).media(task_id, sample_id)
    return FileResponse(
        media.path,
        media_type=media.media_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/filesystem/directories", response_model=DirectoryListingResponse)
def directories(
    request: Request,
    path: Annotated[str | None, Query(max_length=32767)] = None,
) -> DirectoryListingResponse:
    try:
        view = _service(request).directories(path)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return DirectoryListingResponse.from_view(view)


@router.post(
    "/filesystem/select-directory",
    response_model=DirectorySelectionResponse,
)
def select_directory(
    payload: DirectorySelectionRequest,
    request: Request,
) -> DirectorySelectionResponse:
    try:
        path = select_windows_directory(
            project_root=Path(request.app.state.project_root),
            purpose=payload.purpose,
            initial_path=payload.initial_path,
            picker_host=getattr(request.app.state, "native_picker_host", None),
        )
    except DirectoryDialogBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DirectoryDialogUnavailable as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    except DirectoryDialogError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return DirectorySelectionResponse(path=path, cancelled=path is None)


@router.post(
    "/filesystem/select-file",
    response_model=DirectorySelectionResponse,
)
def select_file(
    payload: FileSelectionRequest,
    request: Request,
) -> DirectorySelectionResponse:
    try:
        path = select_windows_file(
            project_root=Path(request.app.state.project_root),
            purpose=payload.purpose,
            initial_path=payload.initial_path,
            picker_host=getattr(request.app.state, "native_picker_host", None),
        )
    except DirectoryDialogBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DirectoryDialogUnavailable as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    except DirectoryDialogError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return DirectorySelectionResponse(path=path, cancelled=path is None)
