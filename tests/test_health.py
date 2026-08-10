from datetime import UTC, datetime

from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.migrate import upgrade_database
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from dataset_audit_studio.model_adapters.registry import DEFAULT_REGISTRY
from fastapi.testclient import TestClient


def test_health_reports_isolated_runtime_and_database(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "health.db")
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["runtime"]["isolated"] is True
    assert payload["runtime"]["user_site_enabled"] is False
    assert payload["database"]["journal_mode"] == "wal"
    assert payload["database"]["foreign_keys"] is True
    assert payload["worker"]["enabled"] is False
    assert payload["models"]["registered_models"] == len(DEFAULT_REGISTRY.all())
    assert payload["models"]["remote_code_allowed"] is False
    assert payload["models"]["models_root"] == str((tmp_path / "models").resolve())


def test_unknown_api_route_is_not_frontend_fallback(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "not-found.db")
    with TestClient(app) as client:
        response = client.get("/api/not-a-route")

    assert response.status_code == 404


def test_custom_project_root_serves_its_own_frontend_dist(tmp_path) -> None:
    project = tmp_path / "project"
    frontend_dist = project / "frontend" / "dist"
    frontend_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<h1>isolated frontend</h1>", encoding="utf-8")
    app = create_app(
        database_path=tmp_path / "frontend.db",
        enforce_runtime=False,
        project_root=project,
    )
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.text == "<h1>isolated frontend</h1>"


def test_app_startup_recovers_expired_worker_lease(tmp_path) -> None:
    database_path = tmp_path / "recovery.db"
    database = Database(database_path, enforce_project_boundary=False)
    upgrade_database(database)
    service = TaskService(database, clock=lambda: datetime(2020, 1, 1, tzinfo=UTC))
    task = service.create_task(
        name="recover",
        source_root=str(tmp_path),
        output_root=None,
        config=ComponentTaskConfigMaterializer().materialize(
            materialize_profile("general")["components"],
            profile="general",
            require_profile=True,
        ),
    )
    service.queue_task(task.id)
    assert service.claim_next(owner="crashed-worker", lease_seconds=5) is not None
    database.dispose()

    app = create_app(database_path=database_path)
    with TestClient(app) as client:
        recovered = client.get(f"/api/tasks/{task.id}").json()

    assert recovered["status"] == "paused"
    assert recovered["resume_state"] == "scanning"
    assert recovered["error_code"] == "worker_lease_expired"
