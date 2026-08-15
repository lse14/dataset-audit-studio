from __future__ import annotations

from pathlib import Path

import pytest
from dataset_audit_studio.adapters.dataset_workspace import DatasetWorkspaceRepository
from dataset_audit_studio.app.component_registration import (
    BUILTIN_COMPONENT_REGISTRATION_CATALOG,
)
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.core.profile_contracts import DatasetProfile
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import Sample
from dataset_audit_studio.jobs.service import TaskService


def test_r10_1_catalog_removes_three_stage_and_keeps_semantic_review_defaults() -> None:
    assert "selection.three_stage" not in BUILTIN_COMPONENT_REGISTRATION_CATALOG.component_ids
    materialized = materialize_profile(DatasetProfile.GENERAL)
    components = materialized["components"]
    assert "selection.three_stage" not in components
    assert components["embedding.semantic"]["enabled"] is True
    assert components["cluster.hierarchy"]["enabled"] is True


def test_r10_1_task_status_removes_stage_selection() -> None:
    assert "STAGE_SELECTION" not in TaskStatus.__members__
    assert "stage_selection" not in {status.value for status in TaskStatus}


def test_r10_1_workspace_uses_valid_samples_without_stage_membership(
    database, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    image = source / "artist" / "sample.png"
    image.parent.mkdir()
    image.write_bytes(b"fixture")
    task_config = materialize_profile("general")
    task = TaskService(database).create_task(
        name="r10.1",
        source_root=str(source),
        output_root=None,
        config=task_config,
    )
    with database.write_session() as session:
        session.add(
            Sample(
                id="sample-1",
                task_id=task.id,
                relative_path="artist/sample.png",
                source_size=7,
                source_mtime_ns=1,
                source_sha256="a" * 64,
                media_kind="image",
                artist_scope="artist",
                scan_state="valid",
                encoded_width=1024,
                encoded_height=1024,
            )
        )
    with database.read_session() as session:
        workspace = DatasetWorkspaceRepository().load(session, task)
    assert [sample.sample_id for sample in workspace.samples] == ["sample-1"]
    assert workspace.datasets


def test_r10_1_old_stage_input_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unknown component configs"):
        ComponentTaskConfigMaterializer().materialize(
            {"selection.three_stage": {"config": {"profile": "general"}}}
        )


def test_r10_1_semantic_defaults_enable_clustering() -> None:
    profile = materialize_profile("general")
    materialized = ComponentTaskConfigMaterializer().materialize(
        profile["components"], profile="general", require_profile=True
    )
    assert materialized["clustering"]["enabled"] is True
