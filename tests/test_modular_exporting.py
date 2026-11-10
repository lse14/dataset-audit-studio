from __future__ import annotations

from dataset_audit_studio.app.component_catalog import build_component_registry
from dataset_audit_studio.app.component_schema_catalog import (
    CONFIG_MODELS,
    UI_CONTRACTS,
)
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer


def _component_payloads() -> dict[str, dict[str, object]]:
    return {
        component_id: {
            "enabled": UI_CONTRACTS[component_id].activation == "required",
            "config": model().model_dump(mode="json"),
        }
        for component_id, model in CONFIG_MODELS.items()
    }


def test_component_materializer_exposes_annotation_output_configuration() -> None:
    materializer = ComponentTaskConfigMaterializer(build_component_registry())
    components = _component_payloads()
    components["export.dataset"]["config"]["keep_annotation_files"] = False

    config = materializer.materialize(components)

    assert config["export"]["keep_annotation_files"] is False
    assert "capt" + "ion" not in config


def test_component_materializer_ignores_retired_latent_settings() -> None:
    materializer = ComponentTaskConfigMaterializer(build_component_registry())
    components = _component_payloads()
    assert "latent.resolve" not in components
    # Legacy task configs and presets may still carry the removed latent export options.
    components["latent.resolve"] = {
        "enabled": True,
        "config": {"mikazuki_enabled": True, "single_file_rules": []},
    }
    components["export.dataset"]["config"]["keep_latent_files"] = False

    config = materializer.materialize(components)

    assert "latent.resolve" not in config["components"]
    assert "latent" not in config
    assert "keep_latent_files" not in config["export"]
    assert "keep_latent_files" not in config["components"]["export.dataset"]["config"]
    normalized = build_component_registry().normalize_task_config(
        {"components": {**config["components"], "latent.resolve": components["latent.resolve"]}}
    )
    assert "latent.resolve" not in normalized
