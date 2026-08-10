from typing import Any

from fastapi import APIRouter, Request

from dataset_audit_studio import __version__
from dataset_audit_studio.jobs.runner import disabled_worker_snapshot
from dataset_audit_studio.runtime import runtime_report

router = APIRouter(tags=["system"])


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    worker = getattr(request.app.state, "worker", None)
    return {
        "status": "ok",
        "app_version": __version__,
        "runtime": runtime_report(),
        "database": request.app.state.database.diagnostics(),
        "worker": worker.snapshot() if worker is not None else disabled_worker_snapshot(),
        "models": request.app.state.model_service.health(),
    }
