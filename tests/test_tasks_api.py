from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from dataset_audit_studio.api import tasks as tasks_api
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.main import create_app
from fastapi.testclient import TestClient


def _components() -> dict:
    return materialize_profile("general")["components"]


def test_component_run_sync_uses_request_registry_for_resolution_and_phase_map(
    monkeypatch,
) -> None:
    resolved = (object(),)
    phase_map_calls = []
    sync_calls = []

    class _Registry:
        def resolve_task_config(self, config):
            assert config == {"components": "request-owned"}
            return resolved

    class _Database:
        def write_session(self):
            return nullcontext(object())

    class _Repository:
        def sync_plan(self, _session, *, task, resolved, phase_by_component) -> None:
            sync_calls.append((task, resolved, phase_by_component))

    registry = _Registry()

    def _phase_map(actual_registry):
        phase_map_calls.append(actual_registry)
        return {"media.scan": "scanning"}

    monkeypatch.setattr(tasks_api, "ComponentRunRepository", _Repository)
    monkeypatch.setattr(tasks_api, "component_phase_map", _phase_map)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                component_registry=registry,
                database=_Database(),
            )
        )
    )
    task = SimpleNamespace(config={"components": "request-owned"})

    tasks_api._sync_component_runs(request, task)

    assert phase_map_calls == [registry]
    assert sync_calls == [(task, resolved, {"media.scan": "scanning"})]


def test_task_api_create_control_and_events(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    app = create_app(database_path=tmp_path / "api.db")

    with TestClient(app) as client:
        created_response = client.post(
            "/api/tasks",
            json={
                "name": "  local dataset  ",
                "source_root": str(source),
                "output_root": str(output),
                "profile": "general",
                "components": _components(),
            },
        )
        assert created_response.status_code == 201
        created = created_response.json()
        assert created["name"] == "local dataset"
        assert created["status"] == "draft"
        component_runs = client.get(f"/api/components/runs/{created['id']}").json()
        assert component_runs["total"] > 0
        assert {item["status"] for item in component_runs["items"]} == {"pending"}

        listed = client.get("/api/tasks", params={"status": "draft"}).json()
        assert listed["total"] == 1
        assert listed["items"][0]["id"] == created["id"]

        queued = client.post(
            f"/api/tasks/{created['id']}/queue",
            json={"expected_version": created["row_version"]},
        ).json()
        assert queued["status"] == "queued"

        paused = client.post(
            f"/api/tasks/{created['id']}/pause",
            json={"expected_version": queued["row_version"]},
        ).json()
        assert paused["status"] == "paused"

        resumed = client.post(
            f"/api/tasks/{created['id']}/resume",
            json={"expected_version": paused["row_version"]},
        ).json()
        assert resumed["status"] == "queued"

        terminated = client.post(
            f"/api/tasks/{created['id']}/terminate",
            json={"expected_version": resumed["row_version"], "reason": "test"},
        ).json()
        assert terminated["status"] == "terminated"

        events = client.get(f"/api/tasks/{created['id']}/events").json()
        assert events["next_after"] == len(events["items"])
        assert events["latest_sequence"] == len(events["items"])
        assert [item["sequence"] for item in events["items"]] == list(
            range(1, len(events["items"]) + 1)
        )
        page = client.get(
            f"/api/tasks/{created['id']}/events",
            params={"after": 1, "limit": 1},
        ).json()
        assert page["next_after"] == 2
        assert page["latest_sequence"] == events["latest_sequence"]


def test_task_api_rejects_unsafe_paths_and_stale_versions(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    app = create_app(database_path=tmp_path / "validation.db")

    with TestClient(app) as client:
        inside_source = client.post(
            "/api/tasks",
            json={
                "name": "unsafe",
                "source_root": str(source),
                "output_root": str(source / "output"),
                "profile": "general",
                "components": _components(),
            },
        )
        assert inside_source.status_code == 422

        missing_source = client.post(
            "/api/tasks",
            json={
                "name": "missing",
                "source_root": str(tmp_path / "missing"),
                "profile": "general",
                "components": _components(),
            },
        )
        assert missing_source.status_code == 422

        invalid_components = _components()
        invalid_components["media.scan"]["config"]["resolutions"] = []
        invalid_scan = client.post(
            "/api/tasks",
            json={
                "name": "invalid scan",
                "source_root": str(source),
                "profile": "general",
                "components": invalid_components,
            },
        )
        assert invalid_scan.status_code == 422
        assert "At least one resolution" in invalid_scan.json()["detail"]

        created = client.post(
            "/api/tasks",
            json={
                "name": "versioned",
                "source_root": str(source),
                "profile": "general",
                "components": _components(),
            },
        ).json()
        queued = client.post(
            f"/api/tasks/{created['id']}/queue",
            json={"expected_version": created["row_version"]},
        )
        assert queued.status_code == 200
        stale = client.post(
            f"/api/tasks/{created['id']}/pause",
            json={"expected_version": created["row_version"]},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "TaskVersionConflict"


def test_removed_api_key_route_is_not_registered(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "credentials.db")

    with TestClient(app) as client:
        legacy_segment = "cap" + "tion-api-key"
        response = client.get(f"/api/tasks/not-a-task/{legacy_segment}")

    assert response.status_code == 404
    assert response.json() == {"detail": "API route not found"}


def test_task_delete_removes_terminal_record_and_project_cache_only(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_marker = source / "keep-source.txt"
    source_marker.write_text("source", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    output_marker = output / "keep-output.txt"
    output_marker.write_text("output", encoding="utf-8")
    app = create_app(database_path=tmp_path / "delete.db", project_root=tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/api/tasks",
            json={
                "name": "delete me",
                "source_root": str(source),
                "output_root": str(output),
                "profile": "general",
                "components": _components(),
            },
        ).json()
        draft_delete = client.request(
            "DELETE",
            f"/api/tasks/{created['id']}",
            json={"expected_version": created["row_version"]},
        )
        assert draft_delete.status_code == 409

        queued = client.post(
            f"/api/tasks/{created['id']}/queue",
            json={"expected_version": created["row_version"]},
        ).json()
        terminal = client.post(
            f"/api/tasks/{created['id']}/terminate",
            json={"expected_version": queued["row_version"]},
        ).json()
        assert terminal["status"] == "terminated"

        cache = tmp_path / "data" / "tasks" / created["id"]
        cache.mkdir(parents=True)
        (cache / "cache.bin").write_bytes(b"task cache")

        deleted = client.request(
            "DELETE",
            f"/api/tasks/{created['id']}",
            json={"expected_version": terminal["row_version"]},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {
            "task_id": created["id"],
            "cache_cleared": True,
            "cache_cleanup_error": None,
        }
        assert not cache.exists()
        assert source_marker.read_text(encoding="utf-8") == "source"
        assert output_marker.read_text(encoding="utf-8") == "output"
        assert client.get(f"/api/tasks/{created['id']}").status_code == 404
        assert client.get("/api/tasks").json()["total"] == 0
