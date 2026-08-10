from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dataset_audit_studio import __version__
from dataset_audit_studio.api.components import router as components_router
from dataset_audit_studio.api.health import router as health_router
from dataset_audit_studio.api.models import router as models_router
from dataset_audit_studio.api.presets import router as presets_router
from dataset_audit_studio.api.reviews import router as reviews_router
from dataset_audit_studio.api.tasks import router as tasks_router
from dataset_audit_studio.api.workspace import router as workspace_router
from dataset_audit_studio.app.component_catalog import build_component_registry
from dataset_audit_studio.app.worker_composition import build_registry_task_runner
from dataset_audit_studio.database.migrate import default_database_path, upgrade_database
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.export_runs.executor import ExportRunExecutor
from dataset_audit_studio.jobs.errors import (
    LegacyTaskConfigUnsupported,
    TaskDomainError,
    TaskNotFound,
)
from dataset_audit_studio.jobs.runner import LocalWorker
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.model_adapters.downloads import ModelDownloadManager
from dataset_audit_studio.model_adapters.errors import (
    ModelIntegrityError,
    ModelOperationConflict,
    ModelRegistryError,
    ModelSchemaError,
)
from dataset_audit_studio.model_adapters.registry import ModelNotFound
from dataset_audit_studio.model_adapters.service import ModelService
from dataset_audit_studio.model_adapters.storage import ModelStorage
from dataset_audit_studio.runtime import (
    PROJECT_ROOT,
    assert_runtime_isolated,
    ensure_runtime_directories,
    runtime_paths,
)


def create_app(
    *,
    database_path: Path | None = None,
    enforce_runtime: bool = True,
    start_worker: bool | None = None,
    models_root: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> FastAPI:
    resolved_database_path = database_path or default_database_path()
    enforce_database_boundary = database_path is None
    worker_enabled = database_path is None if start_worker is None else start_worker
    resolved_models_root = (
        models_root
        if models_root is not None
        else (
            runtime_paths().models
            if database_path is None
            else resolved_database_path.parent / "models"
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if enforce_runtime:
            assert_runtime_isolated()
        ensure_runtime_directories()
        database = Database(
            resolved_database_path,
            enforce_project_boundary=enforce_database_boundary,
        )
        upgrade_database(database)
        app.state.database = database
        app.state.project_root = str(project_root.resolve(strict=False))
        app.state.component_registry = build_component_registry()
        app.state.recovered_tasks = TaskService(database).recover_stale_leases()
        model_storage = ModelStorage(models_root=resolved_models_root)
        model_downloads = ModelDownloadManager(model_storage)
        model_service = ModelService(model_storage, model_downloads)
        app.state.model_service = model_service
        worker = (
            LocalWorker(
                database,
                model_service=model_service,
                scoring_subprocess=enforce_runtime,
                project_root=project_root,
                runner_factory=build_registry_task_runner,
                export_run_runner=ExportRunExecutor(
                    database,
                    project_root=project_root,
                ),
            )
            if worker_enabled
            else None
        )
        app.state.worker = worker
        if worker is not None:
            worker.start()
        try:
            yield
        finally:
            worker_stopped = worker is None or worker.stop()
            model_service.shutdown()
            if worker_stopped:
                database.dispose()

    application = FastAPI(
        title="Dataset Audit Studio",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.include_router(health_router, prefix="/api")
    application.include_router(components_router, prefix="/api")
    application.include_router(models_router, prefix="/api")
    application.include_router(presets_router, prefix="/api")
    application.include_router(tasks_router, prefix="/api")
    application.include_router(reviews_router, prefix="/api")
    application.include_router(workspace_router, prefix="/api")

    @application.exception_handler(TaskNotFound)
    async def task_not_found(_: Request, error: TaskNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": "task_not_found", "detail": str(error)},
        )

    @application.exception_handler(TaskDomainError)
    async def task_conflict(_: Request, error: TaskDomainError) -> JSONResponse:
        if isinstance(error, LegacyTaskConfigUnsupported):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "legacy_task_config_unsupported",
                    "detail": str(error),
                },
            )
        return JSONResponse(
            status_code=409,
            content={"code": error.__class__.__name__, "detail": str(error)},
        )

    @application.exception_handler(ModelNotFound)
    async def model_not_found(_: Request, error: ModelNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": "model_not_found", "detail": str(error)},
        )

    @application.exception_handler(ModelOperationConflict)
    async def model_operation_conflict(_: Request, error: ModelOperationConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": error.__class__.__name__, "detail": str(error)},
        )

    @application.exception_handler(ModelRegistryError)
    async def model_registry_error(_: Request, error: ModelRegistryError) -> JSONResponse:
        status_code = 422 if isinstance(error, ModelSchemaError) else 409
        if isinstance(error, ModelIntegrityError):
            status_code = 409
        return JSONResponse(
            status_code=status_code,
            content={"code": error.__class__.__name__, "detail": str(error)},
        )

    _mount_frontend(application, project_root=project_root)
    return application


def _mount_frontend(application: FastAPI, *, project_root: Path) -> None:
    frontend_dist = project_root.resolve(strict=False) / "frontend" / "dist"
    if not frontend_dist.is_dir():
        return
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @application.get("/{requested_path:path}", include_in_schema=False)
    async def frontend(requested_path: str) -> FileResponse:
        if requested_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")

        candidate = (frontend_dist / requested_path).resolve(strict=False)
        try:
            candidate.relative_to(frontend_dist.resolve())
        except ValueError as error:
            raise HTTPException(status_code=404, detail="File not found") from error

        if requested_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")


app = create_app()
