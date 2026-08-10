from __future__ import annotations

from pathlib import Path

from dataset_audit_studio.adapters.component_run_repository import (
    ComponentRunRepository,
)
from dataset_audit_studio.app.component_catalog import component_phase_map
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from fastapi.testclient import TestClient


def test_component_manifest_api_exposes_the_validated_composition_root(tmp_path: Path) -> None:
    app = create_app(
        database_path=tmp_path / "components.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
    )
    with TestClient(app) as client:
        response = client.get("/api/components")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 14
        assert len({item["id"] for item in payload["items"]}) == 14
        assert all(item["failure_policy"] == "stop" for item in payload["items"])
        assert all(item["json_schema"]["type"] == "object" for item in payload["items"])
        assert all(isinstance(item["default_config"], dict) for item in payload["items"])
        assert {item["ui_group"] for item in payload["items"]} == {
            "input",
            "screening",
            "analysis",
            "selection",
            "output",
        }
        legacy_prefix = "caption" + "."
        assert not [item for item in payload["items"] if item["id"].startswith(legacy_prefix)]

        aesthetic = client.get("/api/components/score.aesthetic_domain")
        assert aesthetic.status_code == 200
        item = aesthetic.json()
        assert item["execution"] == "gpu_process"
        assert item["produces"] == ["score.aesthetic.v1", "score.domain.v1"]
        assert item["model_ids"] == [
            "aesthetic_lse14_5k",
            "jtp3_hydra",
            "waifu_scorer_v3",
        ]
        assert {
            "device",
            "precision",
            "model_id",
            "in_domain_threshold",
            "jtp_max_sequence",
        } <= set(item["json_schema"]["properties"])
        assert item["activation"] == "optional"
        assert item["recommended_enabled"] is True
        assert item["default_config"]["model_id"] == "aesthetic_lse14_5k"

        ai_detection = client.get("/api/components/detect.ai")
        assert ai_detection.status_code == 200
        ai_config = ai_detection.json()["default_config"]
        assert ai_config["model_id"] == "community_forensics_model_384"
        assert ai_config["candidate_threshold"] == 0.121558
        assert ai_config["reference_threshold"] == 0.464626
        missing = client.get("/api/components/not.registered")
        assert missing.status_code == 404


def test_builtin_profile_api_locks_character_consistency_controls(tmp_path: Path) -> None:
    app = create_app(
        database_path=tmp_path / "profile-controls.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
    )
    source = tmp_path / "source"
    source.mkdir()
    with TestClient(app) as client:
        response = client.get("/api/components/builtin-profiles")

        assert response.status_code == 200
        profiles = {item["id"]: item for item in response.json()["items"]}
        assert profiles["artist_concept"]["profile_owned_component_ids"] == ["style.artist"]
        assert profiles["general"]["profile_owned_component_ids"] == ["style.artist"]
        character = profiles["character_concept"]
        assert character["profile_owned_component_ids"] == [
            "style.artist",
            "embedding.semantic",
            "cluster.hierarchy",
        ]
        assert character["profile_owned_config_fields"] == {
            "style.artist": ["enabled"],
            "cluster.hierarchy": ["scope_mode"],
        }

        components = character["components"]
        components["embedding.semantic"]["enabled"] = False
        components["cluster.hierarchy"]["enabled"] = False
        components["cluster.hierarchy"]["config"]["scope_mode"] = "global"
        created = client.post(
            "/api/tasks",
            json={
                "name": "locked character profile",
                "source_root": str(source),
                "profile": "character_concept",
                "components": components,
            },
        )

    assert created.status_code == 201, created.text
    task_config = created.json()["config"]
    assert task_config["components"]["embedding.semantic"]["enabled"] is True
    assert task_config["components"]["cluster.hierarchy"]["enabled"] is True
    assert task_config["clustering"]["scope_mode"] == "concept"


def test_runtime_tuning_api_reports_safe_component_updates(tmp_path: Path) -> None:
    app = create_app(
        database_path=tmp_path / "runtime-tuning.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
    )
    with TestClient(app) as client:
        response = client.get("/api/components/runtime-tuning/recommendation")

    assert response.status_code == 200
    payload = response.json()
    assert {"hardware", "device", "precision", "updates"} == set(payload)
    assert payload["device"] in {"cpu", "cuda"}
    assert payload["precision"] in {"float32", "float16"}
    assert payload["updates"]["feature.clip_l14"]["device"] == payload["device"]
    assert payload["updates"]["feature.clip_l14"]["precision"] == payload["precision"]
    assert 1 <= payload["updates"]["feature.clip_l14"]["batch_size"] <= 64
    assert 1 <= payload["updates"]["analysis.sae"]["batch_size"] <= 4096


def test_component_run_api_returns_current_persisted_plan(tmp_path: Path) -> None:
    app = create_app(
        database_path=tmp_path / "component-runs.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
    )
    source = tmp_path / "source"
    source.mkdir()
    with TestClient(app) as client:
        tasks = TaskService(app.state.database)
        task = tasks.create_task(
            name="component run api",
            source_root=str(source),
            output_root=str(tmp_path / "output"),
            config=ComponentTaskConfigMaterializer(app.state.component_registry).materialize(
                materialize_profile("general")["components"],
                profile="general",
                require_profile=True,
            ),
        )
        resolved = app.state.component_registry.resolve_task_config(task.config)
        with app.state.database.write_session() as session:
            ComponentRunRepository().sync_plan(
                session,
                task=task,
                resolved=resolved,
                phase_by_component=component_phase_map(),
            )
        response = client.get(f"/api/components/runs/{task.id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["task_id"] == task.id
        assert payload["config_hash"] == task.config_hash
        assert payload["total"] == len(resolved)
        by_id = {item["component_id"]: item for item in payload["items"]}
        assert by_id["export.dataset"]["dependency_ids"] == []
        missing = client.get("/api/components/runs/not-a-task")
        assert missing.status_code == 404


def test_schema_driven_task_create_persists_components_and_compatibility_config(
    tmp_path: Path,
) -> None:
    app = create_app(
        database_path=tmp_path / "schema-task.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
    )
    source = tmp_path / "source"
    source.mkdir()
    with TestClient(app) as client:
        manifests = client.get("/api/components").json()["items"]
        components = {
            item["id"]: {
                "enabled": item["activation"] == "required"
                or (
                    item["activation"] == "optional"
                    and item["recommended_enabled"]
                ),
                "config": item["default_config"],
            }
            for item in manifests
        }
        components["media.scan"]["config"]["recursive"] = False
        components["media.scan"]["config"]["resolutions"] = [1216, 1536]
        components["score.aesthetic_domain"]["config"][
            "in_domain_threshold"
        ] = 0.61
        response = client.post(
            "/api/tasks",
            json={
                "name": "schema task",
                "source_root": str(source),
                "output_root": str(tmp_path / "output"),
                "profile": "artist_concept",
                "components": components,
            },
        )
        assert response.status_code == 201, response.text
        task = response.json()
        assert set(task["config"]["components"]) == set(components)
        assert task["config"]["components"]["feature.clip_l14"]["enabled"] is False
        assert task["config"]["scan"]["recursive"] is False
        assert task["config"]["scan"]["resolutions"] == [1216, 1536]
        assert task["config"]["scoring"]["aesthetic"]["in_domain_threshold"] == 0.61
        assert "caption" not in task["config"]

        runs = client.get(f"/api/components/runs/{task['id']}")
        assert runs.status_code == 200
        by_id = {item["component_id"]: item for item in runs.json()["items"]}
        assert by_id["feature.clip_l14"]["auto_enabled"] is True
        assert by_id["score.aesthetic_domain"]["normalized_config"]["config"][
            "in_domain_threshold"
        ] == 0.61



def test_schema_driven_task_create_rejects_required_component_disable(tmp_path: Path) -> None:
    app = create_app(
        database_path=tmp_path / "invalid-schema-task.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
    )
    source = tmp_path / "source"
    source.mkdir()
    with TestClient(app) as client:
        manifests = client.get("/api/components").json()["items"]
        components = {
            item["id"]: {
                "enabled": item["activation"] == "required",
                "config": item["default_config"],
            }
            for item in manifests
        }
        components["media.scan"]["enabled"] = False
        response = client.post(
            "/api/tasks",
            json={
                "name": "invalid schema task",
                "source_root": str(source),
                "profile": "artist_concept",
                "components": components,
            },
        )
        assert response.status_code == 422
        assert "Required component cannot be disabled: media.scan" in response.text
