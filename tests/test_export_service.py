from __future__ import annotations

import pytest
from dataset_audit_studio.components.dataset_export.config import DatasetExportConfig
from dataset_audit_studio.components.dataset_export.manifest import MANIFEST
from pydantic import ValidationError


def test_export_config_defaults_and_validates_batch_size() -> None:
    config = DatasetExportConfig.from_task_config({})
    assert config.batch_size == 64
    assert config.keep_annotation_files is True
    assert config.keep_latent_files is True
    assert "keep_caption_files" not in config.model_dump()
    with pytest.raises(ValidationError):
        DatasetExportConfig(batch_size=0)


def test_export_accepts_optional_latents_without_selection_or_caption() -> None:
    capabilities = {item.capability: item.optional for item in MANIFEST.consumes}
    assert capabilities == {"latent.reference.v1": True}
