from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from dataset_audit_studio.main import create_app
from dataset_audit_studio.presets.service import (
    TaskPresetNameConflict,
    TaskPresetNotFound,
    TaskPresetService,
    TaskPresetVersionConflict,
)
from fastapi.testclient import TestClient


def _default_components(
    client: TestClient,
    profile: str = "artist_concept",
) -> dict[str, dict]:
    response = client.get("/api/components/builtin-profiles")
    assert response.status_code == 200
    selected = next(item for item in response.json()["items"] if item["id"] == profile)
    return copy.deepcopy(selected["components"])


def test_task_preset_service_crud_and_conflicts(database) -> None:
    service = TaskPresetService(database)
    created = service.create(
        name="  My Preset  ",
        components={"component": {"enabled": True}},
        profile="general",
    )
    assert created.name == "My Preset"
    assert created.row_version == 1
    listed = service.list_presets()
    assert len(listed) == 1
    assert listed[0].id == created.id
    assert listed[0].name == created.name
    assert listed[0].components == created.components

    with pytest.raises(TaskPresetNameConflict):
        service.create(name="my preset", components={}, profile="general")

    updated = service.update(
        created.id,
        name="Renamed",
        components={"component": {"enabled": False}},
        profile="general",
        expected_version=created.row_version,
    )
    assert updated.name == "Renamed"
    assert updated.components["component"]["enabled"] is False
    assert updated.row_version == 2

    with pytest.raises(TaskPresetVersionConflict):
        service.update(
            created.id,
            name="stale",
            components={},
            profile="general",
            expected_version=1,
        )
    with pytest.raises(TaskPresetVersionConflict):
        service.delete(created.id, expected_version=1)

    deleted = service.delete(created.id, expected_version=2)
    assert deleted.id == created.id
    assert service.list_presets() == []
    with pytest.raises(TaskPresetNotFound):
        service.delete(created.id, expected_version=2)


def test_task_preset_api_crud_validates_components_and_preserves_tasks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    app = create_app(database_path=tmp_path / "presets.db", enforce_runtime=False)

    with TestClient(app) as client:
        components = _default_components(client)
        created_task = client.post(
            "/api/tasks",
            json={
                "name": "preset source",
                "source_root": str(source),
                "profile": "artist_concept",
                "components": components,
            },
        ).json()
        task_config_before = json.dumps(created_task["config"], sort_keys=True)

        created_response = client.post(
            "/api/task-presets",
            json={"name": "Default Tune", "profile": "artist_concept", "components": components},
        )
        assert created_response.status_code == 201
        created = created_response.json()
        assert created["name"] == "Default Tune"
        assert created["row_version"] == 1
        assert created["components"]["cluster.hierarchy"]["config"]["scope_mode"] == "concept"
        assert created["profile"] == "artist_concept"
        assert "source_root" not in created
        assert "output_root" not in created

        duplicate = client.post(
            "/api/task-presets",
            json={"name": "default tune", "profile": "artist_concept", "components": components},
        )
        assert duplicate.status_code == 409

        components = _default_components(client, "general")
        updated_response = client.put(
            f"/api/task-presets/{created['id']}",
            json={
                "name": "Global Tune",
                "profile": "general",
                "components": components,
                "expected_version": created["row_version"],
            },
        )
        assert updated_response.status_code == 200
        updated = updated_response.json()
        assert updated["name"] == "Global Tune"
        assert updated["row_version"] == 2
        assert updated["components"]["cluster.hierarchy"]["config"]["scope_mode"] == "global"
        assert updated["profile"] == "general"

        stale = client.put(
            f"/api/task-presets/{created['id']}",
            json={
                "name": "Stale",
                "profile": "general",
                "components": components,
                "expected_version": 1,
            },
        )
        assert stale.status_code == 409

        invalid_components = json.loads(json.dumps(components))
        invalid_components["export.dataset"]["config"]["unknown"] = "forbidden"
        rejected = client.post(
            "/api/task-presets",
            json={"name": "secret", "profile": "general", "components": invalid_components},
        )
        assert rejected.status_code == 422
        assert "unknown" in rejected.json()["detail"]

        stale_delete = client.request(
            "DELETE",
            f"/api/task-presets/{created['id']}",
            json={"expected_version": 1},
        )
        assert stale_delete.status_code == 409
        deleted = client.request(
            "DELETE",
            f"/api/task-presets/{created['id']}",
            json={"expected_version": updated["row_version"]},
        )
        assert deleted.json() == {"preset_id": created["id"]}
        assert client.get("/api/task-presets").json() == {"items": [], "total": 0}

        unchanged = client.get(f"/api/tasks/{created_task['id']}").json()
        assert json.dumps(unchanged["config"], sort_keys=True) == task_config_before


def test_task_preset_api_creates_from_profile_tasks_and_rejects_legacy_payloads(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    app = create_app(database_path=tmp_path / "from-task.db", enforce_runtime=False)

    with TestClient(app) as client:
        components = _default_components(client, "general")
        current = client.post(
            "/api/tasks",
            json={
                "name": "current",
                "source_root": str(source),
                "profile": "general",
                "components": components,
            },
        ).json()
        legacy = client.post(
            "/api/tasks",
            json={
                "name": "legacy",
                "source_root": str(source),
                "config": {"clustering": {"scope_mode": "artist"}},
            },
        )

        current_preset = client.post(
            f"/api/task-presets/from-task/{current['id']}",
            json={"name": "From Current"},
        )
        assert current_preset.status_code == 201
        assert legacy.status_code == 422
        assert legacy.json()["code"] == "legacy_task_config_unsupported"
        assert (
            current_preset.json()["components"]["cluster.hierarchy"]["config"]["scope_mode"]
            == "global"
        )
        assert current_preset.json()["profile"] == "general"
        missing = client.post(
            "/api/task-presets/from-task/does-not-exist",
            json={"name": "missing"},
        )
        assert missing.status_code == 404
