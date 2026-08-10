from __future__ import annotations

from pathlib import Path

import pytest
from alembic.util.exc import CommandError
from dataset_audit_studio.database import enums, models
from dataset_audit_studio.database.base import Base
from dataset_audit_studio.database.migrate import (
    downgrade_database,
    migration_head,
    upgrade_database,
)
from dataset_audit_studio.database.session import Database
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

BASELINE_REVISION = "0001_clean_slate_schema"
LEGACY_REVISION = "0007_export_run_input_snapshot"
RETIRED_MODELS = {"StageMembership", "Export", "LatentEntry"}
RETIRED_TABLES = {"stage_memberships", "exports", "latent_entries"}
CURRENT_TABLES = {
    "artifacts",
    "cluster_memberships",
    "cluster_nodes",
    "component_runs",
    "evidence",
    "export_runs",
    "model_results",
    "phase_checkpoints",
    "resolution_assessments",
    "review_decisions",
    "samples",
    "task_configs",
    "task_events",
    "task_presets",
    "tasks",
    "worker_leases",
}


def _insert_task_state(
    database: Database, *, task_id: str, status: str, resume_state: str | None
) -> None:
    with database.write_session() as session:
        session.execute(
            text(
                """
                INSERT INTO tasks (
                    id, name, source_root, status, resume_state, current_config_revision,
                    progress_current, row_version, execution_epoch
                ) VALUES (
                    :id, :name, :source_root, :status, :resume_state, 1, 0, 1, 0
                )
                """
            ),
            {
                "id": task_id,
                "name": task_id,
                "source_root": "/temporary/source",
                "status": status,
                "resume_state": resume_state,
            },
        )


def test_clean_slate_schema_has_one_baseline_revision() -> None:
    versions = Path(models.__file__).parent / "migrations" / "versions"

    assert sorted(path.name for path in versions.glob("*.py")) == [
        "0001_clean_slate_schema.py"
    ]
    assert migration_head() == BASELINE_REVISION


def test_clean_slate_schema_removes_retired_runtime_definitions() -> None:
    assert not RETIRED_MODELS.intersection(vars(models))
    assert not RETIRED_TABLES.intersection(Base.metadata.tables)
    assert not hasattr(enums, "ExportState")
    assert enums.TaskStatus.EXPORTING.value == "exporting"


def test_clean_slate_baseline_task_state_constraints_remove_stage_selection(tmp_path) -> None:
    database = Database(tmp_path / "clean-slate-constraints.db", enforce_project_boundary=False)
    try:
        upgrade_database(database)
        constraints = {
            constraint["name"]: constraint["sqltext"]
            for constraint in inspect(database.engine).get_check_constraints("tasks")
        }
        for name in ("ck_tasks_status", "ck_tasks_resume_state"):
            assert "stage_selection" not in constraints[name]
    finally:
        database.dispose()


def test_clean_slate_baseline_accepts_exporting_task_state(tmp_path) -> None:
    database = Database(tmp_path / "clean-slate-valid-state.db", enforce_project_boundary=False)
    try:
        upgrade_database(database)
        _insert_task_state(
            database,
            task_id="valid-exporting",
            status=enums.TaskStatus.EXPORTING.value,
            resume_state=enums.TaskStatus.EVIDENCE_REVIEW.value,
        )
    finally:
        database.dispose()


@pytest.mark.parametrize("column", ("status", "resume_state"))
def test_clean_slate_baseline_rejects_stage_selection_task_state(tmp_path, column: str) -> None:
    database = Database(tmp_path / f"clean-slate-{column}.db", enforce_project_boundary=False)
    try:
        upgrade_database(database)
        values = {"status": enums.TaskStatus.DRAFT.value, "resume_state": None}
        values[column] = "stage_selection"
        with pytest.raises(IntegrityError):
            _insert_task_state(database, task_id=f"invalid-{column}", **values)
    finally:
        database.dispose()


def test_clean_slate_baseline_matches_current_metadata_and_roundtrips(tmp_path) -> None:
    database = Database(tmp_path / "clean-slate-schema.db", enforce_project_boundary=False)
    try:
        upgrade_database(database)
        expected_tables = {"alembic_version", *CURRENT_TABLES}
        assert set(inspect(database.engine).get_table_names()) == expected_tables
        assert set(Base.metadata.tables) == CURRENT_TABLES
        assert not RETIRED_TABLES.intersection(inspect(database.engine).get_table_names())

        downgrade_database(database)
        assert set(inspect(database.engine).get_table_names()) == {"alembic_version"}

        upgrade_database(database)
        assert set(inspect(database.engine).get_table_names()) == expected_tables
    finally:
        database.dispose()


def test_clean_slate_schema_rejects_legacy_revision(tmp_path) -> None:
    database = Database(tmp_path / "legacy-revision.db", enforce_project_boundary=False)
    try:
        with pytest.raises(CommandError, match="Can't locate revision"):
            upgrade_database(database, LEGACY_REVISION)
    finally:
        database.dispose()
