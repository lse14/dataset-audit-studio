from __future__ import annotations

from pathlib import Path

import pytest
from dataset_audit_studio.adapters.component_run_repository import (
    ComponentRunRepository,
)
from dataset_audit_studio.app.component_catalog import (
    build_component_registry,
    component_phase_map,
)
from dataset_audit_studio.app.component_execution import ComponentExecutionCatalog
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.app.registry_task_runner import RegistryTaskRunner
from dataset_audit_studio.core.component_contracts import (
    ComponentBatchResult,
    ComponentRunRequest,
)
from dataset_audit_studio.database.enums import ComponentRunState, TaskStatus
from dataset_audit_studio.database.models import ComponentRun, PhaseCheckpoint, Task
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from sqlalchemy import func, select


def _phase_map() -> dict[str, str]:
    return component_phase_map(build_component_registry())


def _config() -> dict:
    components = materialize_profile("general")["components"]
    components["export.dataset"]["config"]["mode"] = "rewrite"
    return ComponentTaskConfigMaterializer(build_component_registry()).materialize(
        components,
        profile="general",
        require_profile=True,
    )


def test_component_run_plan_is_idempotent_and_keeps_component_state(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    task = task_service.create_task(
        name="component runs",
        source_root=str(source),
        output_root=str(tmp_path / "output"),
        config=_config(),
    )
    registry = build_component_registry()
    resolved = registry.resolve_task_config(task.config)
    repository = ComponentRunRepository()
    with database.write_session() as session:
        first = repository.sync_plan(
            session,
            task=task,
            resolved=resolved,
            phase_by_component=_phase_map(),
        )
        repository.mark_running(
            session,
            task_id=task.id,
            config_hash=task.config_hash,
            component_ids=("export.dataset",),
        )
        repository.mark_status(
            session,
            task_id=task.id,
            config_hash=task.config_hash,
            component_ids=("export.dataset",),
            status=ComponentRunState.COMPLETED,
        )
    assert len(first) == len(resolved)
    assert len({item.component_id for item in first}) == len(first)

    with database.write_session() as session:
        second = repository.sync_plan(
            session,
            task=task,
            resolved=resolved,
            phase_by_component=_phase_map(),
        )
    exporting = next(item for item in second if item.component_id == "export.dataset")
    assert exporting.status == ComponentRunState.COMPLETED.value
    assert exporting.phase == "exporting"
    assert len(exporting.config_digest) == 64
    assert set(exporting.config_digest) <= set("0123456789abcdef")
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(ComponentRun)) == len(resolved)


def test_component_run_reconciles_versioned_component_checkpoint(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    task = task_service.create_task(
        name="component checkpoint",
        source_root=str(source),
        output_root=str(tmp_path / "output"),
        config=_config(),
    )
    repository = ComponentRunRepository()
    resolved = build_component_registry().resolve_task_config(task.config)
    digest = "a" * 64
    with database.write_session() as session:
        repository.sync_plan(
            session,
            task=task,
            resolved=resolved,
            phase_by_component=_phase_map(),
        )
        session.add(
            PhaseCheckpoint(
                task_id=task.id,
                phase="exporting",
                config_hash=task.config_hash,
                batch_index=0,
                cursor_json={
                    "modular_exporting": True,
                    "component_id": "export.dataset",
                    "identity_digest": digest,
                    "next_index": 7,
                    "component_complete": True,
                },
                completed_items=0,
                execution_epoch=1,
                artifact_id=None,
            )
        )
        session.flush()
        repository.reconcile_phase_checkpoints(
            session,
            task_id=task.id,
            config_hash=task.config_hash,
            phase="exporting",
        )
    with database.read_session() as session:
        row = session.scalar(
            select(ComponentRun).where(
                ComponentRun.task_id == task.id,
                ComponentRun.component_id == "export.dataset",
            )
        )
        assert row is not None
        assert row.status == ComponentRunState.COMPLETED.value
        assert row.input_digest == digest
        assert row.completed_items == 7
        assert row.checkpoint_json["component_complete"] is True


def _prepare_exporting_task(
    database: Database,
    tasks: TaskService,
    tmp_path: Path,
) -> str:
    source = tmp_path / "source"
    source.mkdir()
    task = tasks.create_task(
        name="registry runner",
        source_root=str(source),
        output_root=str(tmp_path / "output"),
        config=_config(),
    )
    with database.write_session() as session:
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.EVIDENCE_REVIEW.value
        row.resume_state = None
    released = tasks.release_review_gate(
        task.id,
        expected_gate=TaskStatus.EVIDENCE_REVIEW,
    )
    assert released.status == TaskStatus.QUEUED.value
    assert released.resume_state == TaskStatus.EXPORTING.value
    return task.id


def _claim(tasks: TaskService, owner: str):
    claimed = tasks.claim_next(owner=owner, lease_seconds=120)
    assert claimed is not None
    assert claimed.task.status == TaskStatus.EXPORTING.value
    return claimed


class _FakeExecutionPort:
    def __init__(self, component_id: str, calls: list[str], *, fail: bool = False) -> None:
        self.component_id = component_id
        self.calls = calls
        self.fail = fail

    def execute(self, request: ComponentRunRequest) -> ComponentBatchResult:
        assert request.component_id == self.component_id
        self.calls.append(self.component_id)
        if self.fail:
            raise RuntimeError("component execution failed")
        return ComponentBatchResult(
            component_id=self.component_id,
            batch_index=0,
            completed_items=0,
            component_complete=True,
            final_status=TaskStatus.EXPORTING.value,
        )


def _fake_execution_catalog(
    tasks: TaskService,
    calls: list[str],
    *,
    failing_component: str | None = None,
    registry=None,
) -> ComponentExecutionCatalog:
    registry = registry or build_component_registry()
    ports = {
        definition.manifest.id: _FakeExecutionPort(
            definition.manifest.id,
            calls,
            fail=definition.manifest.id == failing_component,
        )
        for definition in registry.definitions
    }
    return ComponentExecutionCatalog(
        registry,
        ports,
        finalizers={
            TaskStatus.EXPORTING.value: lambda token, _order: tasks.complete_phase(
                token,
                phase=TaskStatus.EXPORTING,
            ).status
        },
    )


def test_registry_runner_advances_phase_without_worker_business_branching(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _prepare_exporting_task(database, task_service, tmp_path)
    claimed = _claim(task_service, "registry-success")
    calls: list[str] = []
    execution = _fake_execution_catalog(task_service, calls)
    summary = RegistryTaskRunner(task_service, execution).run(
        claimed.token,
        claimed.task,
    )
    assert summary.component_ids == ("export.dataset",)
    assert summary.final_status == TaskStatus.COMPLETED.value
    assert calls == ["export.dataset"]
    with database.read_session() as session:
        runs = ComponentRunRepository().list_for_config(
            session,
            task_id=task_id,
            config_hash=claimed.task.config_hash,
        )
    by_id = {item.component_id: item for item in runs}
    assert by_id["export.dataset"].status == ComponentRunState.COMPLETED.value


def test_registry_runner_defaults_to_execution_registry_for_plan_and_execution(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    _prepare_exporting_task(database, task_service, tmp_path)
    claimed = _claim(task_service, "execution-registry")
    registry = build_component_registry()
    calls: list[str] = []
    execution = _fake_execution_catalog(task_service, calls, registry=registry)

    runner = RegistryTaskRunner(task_service, execution)
    assert runner.registry is registry
    summary = runner.run(claimed.token, claimed.task)

    assert summary.component_ids == ("export.dataset",)
    assert calls == ["export.dataset"]


def test_registry_runner_records_component_failure_and_reraises(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _prepare_exporting_task(database, task_service, tmp_path)
    claimed = _claim(task_service, "registry-failure")
    calls: list[str] = []
    execution = _fake_execution_catalog(
        task_service,
        calls,
        failing_component="export.dataset",
    )
    with pytest.raises(RuntimeError, match="component execution failed"):
        RegistryTaskRunner(task_service, execution).run(claimed.token, claimed.task)
    with database.read_session() as session:
        rows = session.scalars(
            select(ComponentRun).where(
                ComponentRun.task_id == task_id,
                ComponentRun.component_id == "export.dataset",
            )
        ).all()
    by_id = {row.component_id: row for row in rows}
    assert calls == ["export.dataset"]
    assert by_id["export.dataset"].status == ComponentRunState.FAILED.value
    assert by_id["export.dataset"].error_code == "RuntimeError"
    assert by_id["export.dataset"].error_message == "component execution failed"


def test_batch_commit_updates_component_checkpoint_in_same_transaction(
    database: Database,
    tmp_path: Path,
) -> None:
    task_service = TaskService(
        database,
        batch_checkpoint_writer=ComponentRunRepository.apply_batch_checkpoint,
    )
    task_id = _prepare_exporting_task(database, task_service, tmp_path)
    claimed = _claim(task_service, "atomic-component")
    repository = ComponentRunRepository()
    resolved = build_component_registry().resolve_task_config(claimed.task.config)
    with database.write_session() as session:
        repository.sync_plan(
            session,
            task=claimed.task,
            resolved=resolved,
            phase_by_component=_phase_map(),
        )
        repository.mark_running(
            session,
            task_id=task_id,
            config_hash=claimed.task.config_hash,
            component_ids=("export.dataset",),
        )
    digest = "d" * 64
    task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.EXPORTING,
        config_hash=claimed.task.config_hash,
        batch_index=0,
        completed_items=0,
        progress_total=0,
        cursor={
            "modular_exporting": True,
            "component_id": "export.dataset",
            "identity_digest": digest,
            "next_index": 9,
            "component_complete": True,
        },
        lease_seconds=120,
    )
    with database.read_session() as session:
        row = session.scalar(
            select(ComponentRun).where(
                ComponentRun.task_id == task_id,
                ComponentRun.component_id == "export.dataset",
            )
        )
        checkpoint_count = session.scalar(
            select(func.count())
            .select_from(PhaseCheckpoint)
            .where(
                PhaseCheckpoint.task_id == task_id,
                PhaseCheckpoint.phase == TaskStatus.EXPORTING.value,
            )
        )
        assert row is not None
        assert row.status == ComponentRunState.COMPLETED.value
        assert row.input_digest == digest
        assert row.completed_items == 9
        assert row.checkpoint_json["next_index"] == 9
        assert checkpoint_count == 1
