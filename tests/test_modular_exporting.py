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


def test_component_materializer_disables_latent_when_export_retention_is_disabled() -> None:
    materializer = ComponentTaskConfigMaterializer(build_component_registry())
    components = _component_payloads()
    components["latent.resolve"]["enabled"] = True
    components["export.dataset"]["config"]["keep_latent_files"] = False

    config = materializer.materialize(components)

    assert config["latent"]["mikazuki_enabled"] is False
    assert config["latent"]["single_file_rules"] == []
