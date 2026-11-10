from __future__ import annotations

import pytest
from dataset_audit_studio.components.dataset_export.config import DatasetExportConfig
from dataset_audit_studio.components.dataset_export.manifest import MANIFEST
from pydantic import ValidationError


def test_export_config_defaults_and_validates_batch_size() -> None:
    config = DatasetExportConfig.from_task_config({})
    assert config.batch_size == 64
    assert config.keep_annotation_files is True
    assert "keep_latent_files" not in config.model_dump()
    assert "keep_caption_files" not in config.model_dump()
    with pytest.raises(ValidationError):
        DatasetExportConfig(batch_size=0)


def test_export_config_ignores_retired_latent_option() -> None:
    config = DatasetExportConfig.from_task_config(
        {"export": {"keep_latent_files": False, "keep_annotation_files": False}}
    )
    assert config.keep_annotation_files is False
    assert "keep_latent_files" not in config.model_dump()


def test_export_consumes_no_latent_or_caption_capability() -> None:
    assert MANIFEST.consumes == ()
