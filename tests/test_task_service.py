from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import PhaseCheckpoint, Sample
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import (
    CheckpointConflict,
    InvalidTaskTransition,
    StaleWorkerToken,
    TaskNotFound,
    TaskVersionConflict,
    WorkerLeaseUnavailable,
)
from dataset_audit_studio.jobs.service import TaskService
from sqlalchemy import func, select


def _task_config(*, resolutions: tuple[int, ...] = (1024,)) -> dict:
    components = materialize_profile("general")["components"]
    components["media.scan"]["config"]["resolutions"] = list(resolutions)
    return ComponentTaskConfigMaterializer().materialize(
        components, profile="general", require_profile=True
    )


def create_queued(service: TaskService, name: str = "task"):
    task = service.create_task(
        name=name,
        source_root="E:\\dataset",
        output_root="E:\\output",
        config=_task_config(),
    )
    return service.queue_task(task.id, expected_version=task.row_version)


def test_batch_checkpoint_writer_injection_receives_the_commit_session_and_control_state(
    database: Database,
) -> None:
    calls = []

    def writer(session, **kwargs) -> None:
        calls.append((session, kwargs))

    service = TaskService(database, batch_checkpoint_writer=writer)
    queued = create_queued(service, "batch checkpoint writer")
    claimed = service.claim_next(owner="writer", lease_seconds=60)
    assert claimed is not None
    service.request_pause(queued.id, expected_version=claimed.task.row_version)

    committed = service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=1,
        progress_total=1,
        cursor={"component_id": "media.scan", "input_digest": "d" * 64},
    )

    assert committed.control_state == "paused"
    assert len(calls) == 1
    session, kwargs = calls[0]
    assert session is not None
    assert kwargs["task_id"] == queued.id
    assert kwargs["config_hash"] == queued.config_hash
    assert kwargs["cursor"] == {"component_id": "media.scan", "input_digest": "d" * 64}
    assert kwargs["control_state"] == "paused"
    assert isinstance(kwargs["now"], datetime)


def test_pause_resume_commits_only_at_batch_boundary(task_service: TaskService) -> None:
    queued = create_queued(task_service)
    claimed = task_service.claim_next(owner="worker-a", lease_seconds=60)
    assert claimed is not None
    assert claimed.task.status == TaskStatus.SCANNING.value

    pausing = task_service.request_pause(queued.id, expected_version=claimed.task.row_version)
    assert pausing.status == TaskStatus.PAUSING.value
    assert pausing.resume_state == TaskStatus.SCANNING.value

    committed = task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=32,
        progress_total=100,
        cursor={"relative_path": "artist/0032.webp"},
    )
    assert committed.control_state == "paused"
    assert committed.task.status == TaskStatus.PAUSED.value
    assert committed.task.progress_current == 32
    assert committed.task.lease_owner is None

    replayed = task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=32,
        progress_total=100,
        cursor={"relative_path": "artist/0032.webp"},
    )
    assert replayed.replayed is True
    assert replayed.control_state == "paused"
    assert len(task_service.list_checkpoints(queued.id)) == 1

    resumed = task_service.resume_task(queued.id, expected_version=committed.task.row_version)
    assert resumed.status == TaskStatus.QUEUED.value
    claimed_again = task_service.claim_next(owner="worker-b", lease_seconds=60)
    assert claimed_again is not None
    assert claimed_again.task.id == queued.id
    assert claimed_again.task.execution_epoch > claimed.task.execution_epoch


def test_process_stop_recovery_pauses_without_a_checkpoint(task_service: TaskService) -> None:
    queued = create_queued(task_service)
    claimed = task_service.claim_next(owner="worker-a", lease_seconds=60)
    assert claimed is not None

    pausing = task_service.request_pause(queued.id, expected_version=claimed.task.row_version)
    assert pausing.status == TaskStatus.PAUSING.value

    recovered = task_service.recover_worker_after_process_stop(
        claimed.token,
        reason="test child did not return",
    )

    assert recovered.status == TaskStatus.PAUSED.value
    assert recovered.resume_state == TaskStatus.SCANNING.value
    assert recovered.lease_owner is None
    assert task_service.list_checkpoints(queued.id) == []


def test_checkpoint_identity_rejects_different_data(task_service: TaskService) -> None:
    queued = create_queued(task_service)
    claimed = task_service.claim_next(owner="worker", lease_seconds=60)
    assert claimed is not None
    task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=10,
        cursor={"position": 10},
    )
    with pytest.raises(CheckpointConflict):
        task_service.commit_batch(
            claimed.token,
            phase=TaskStatus.SCANNING,
            config_hash=queued.config_hash,
            batch_index=0,
            completed_items=11,
            cursor={"position": 11},
        )


def test_batch_writer_is_atomic_and_not_replayed(
    database: Database, task_service: TaskService
) -> None:
    queued = create_queued(task_service)
    claimed = task_service.claim_next(owner="worker", lease_seconds=60)
    assert claimed is not None

    def failing_writer(session) -> None:
        session.add(
            Sample(
                task_id=queued.id,
                relative_path="rolled-back.png",
                source_size=1,
                source_mtime_ns=1,
                source_sha256="1" * 64,
                media_kind="image",
                artist_scope="__root__",
                scan_state="valid",
            )
        )
        session.flush()
        raise RuntimeError("injected batch failure")

    with pytest.raises(RuntimeError, match="injected batch failure"):
        task_service.commit_batch(
            claimed.token,
            phase=TaskStatus.SCANNING,
            config_hash=queued.config_hash,
            batch_index=0,
            completed_items=1,
            cursor={"position": 1},
            batch_writer=failing_writer,
        )

    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(Sample)) == 0
        assert session.scalar(select(func.count()).select_from(PhaseCheckpoint)) == 0
    assert task_service.get_task(queued.id).progress_current == 0

    calls = 0

    def successful_writer(session) -> None:
        nonlocal calls
        calls += 1
        session.add(
            Sample(
                task_id=queued.id,
                relative_path="committed.png",
                source_size=1,
                source_mtime_ns=1,
                source_sha256="2" * 64,
                media_kind="image",
                artist_scope="__root__",
                scan_state="valid",
            )
        )

    committed = task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=1,
        cursor={"position": 1},
        batch_writer=successful_writer,
    )
    assert committed.replayed is False

    def must_not_run(_session) -> None:
        raise AssertionError("checkpoint replay reran its batch writer")

    replayed = task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=1,
        cursor={"position": 1},
        batch_writer=must_not_run,
    )
    assert replayed.replayed is True
    assert calls == 1


def test_checkpoint_replay_honors_new_control_and_rejects_advanced_phase(
    task_service: TaskService,
) -> None:
    queued = create_queued(task_service)
    claimed = task_service.claim_next(owner="worker", lease_seconds=60)
    assert claimed is not None
    first = task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=10,
        cursor={"position": 10},
    )
    task_service.request_pause(queued.id, expected_version=first.task.row_version)
    replayed = task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=10,
        cursor={"position": 10},
    )
    assert replayed.replayed is True
    assert replayed.control_state == "paused"

    advanced = create_queued(task_service, "advanced")
    claimed_advanced = task_service.claim_next(owner="worker-2", lease_seconds=60)
    assert claimed_advanced is not None
    assert claimed_advanced.task.id == advanced.id
    task_service.commit_batch(
        claimed_advanced.token,
        phase=TaskStatus.SCANNING,
        config_hash=advanced.config_hash,
        batch_index=0,
        completed_items=10,
        cursor={"position": 10},
    )
    task_service.complete_phase(claimed_advanced.token, phase=TaskStatus.SCANNING)
    with pytest.raises(StaleWorkerToken):
        task_service.commit_batch(
            claimed_advanced.token,
            phase=TaskStatus.SCANNING,
            config_hash=advanced.config_hash,
            batch_index=0,
            completed_items=10,
            cursor={"position": 10},
        )


def test_graceful_and_force_termination_invalidate_workers(task_service: TaskService) -> None:
    queued = create_queued(task_service, "graceful")
    claimed = task_service.claim_next(owner="worker-a", lease_seconds=60)
    assert claimed is not None
    terminating = task_service.request_terminate(queued.id, reason="operator request")
    assert terminating.status == TaskStatus.TERMINATING.value
    result = task_service.commit_batch(
        claimed.token,
        phase=TaskStatus.SCANNING,
        config_hash=queued.config_hash,
        batch_index=0,
        completed_items=5,
        cursor={"position": 5},
    )
    assert result.control_state == "terminated"
    assert result.task.status == TaskStatus.TERMINATED.value

    queued_force = create_queued(task_service, "force")
    claimed_force = task_service.claim_next(owner="worker-b", lease_seconds=60)
    assert claimed_force is not None
    terminated = task_service.request_terminate(queued_force.id, force=True)
    assert terminated.status == TaskStatus.TERMINATED.value
    with pytest.raises(StaleWorkerToken):
        task_service.commit_batch(
            claimed_force.token,
            phase=TaskStatus.SCANNING,
            config_hash=queued_force.config_hash,
            batch_index=0,
            completed_items=1,
            cursor={"position": 1},
        )


def test_single_worker_slot_is_atomic(database: Database) -> None:
    service = TaskService(database)
    first = create_queued(service, "first")
    second = create_queued(service, "second")
    barrier = Barrier(2)

    def claim(owner: str):
        barrier.wait()
        try:
            return TaskService(database).claim_next(owner=owner, lease_seconds=60)
        except WorkerLeaseUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["worker-a", "worker-b"]))
    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].task.id in {first.id, second.id}


def test_claim_can_skip_queued_phases_without_handlers(task_service: TaskService) -> None:
    unsupported = create_queued(task_service, "unsupported")
    scan_claim = task_service.claim_next(owner="scan", lease_seconds=60)
    assert scan_claim is not None
    task_service.complete_phase(scan_claim.token, phase=TaskStatus.SCANNING)
    cpu_claim = task_service.claim_next(owner="cpu", lease_seconds=60)
    assert cpu_claim is not None
    model_queued = task_service.complete_phase(cpu_claim.token, phase=TaskStatus.CPU_METRICS)
    assert model_queued.resume_state == TaskStatus.MODEL_SCORING.value

    supported = create_queued(task_service, "supported")
    claimed = task_service.claim_next(
        owner="stage-c-worker",
        lease_seconds=60,
        allowed_phases={TaskStatus.SCANNING, TaskStatus.CPU_METRICS},
    )
    assert claimed is not None
    assert claimed.task.id == supported.id
    assert task_service.get_task(unsupported.id).status == TaskStatus.QUEUED.value


def test_claimed_worker_can_pause_before_first_batch_without_checkpoint(
    task_service: TaskService,
) -> None:
    queued = create_queued(task_service, "shutdown race")
    claimed = task_service.claim_next(owner="worker", lease_seconds=60)
    assert claimed is not None
    task_service.request_pause(queued.id)

    paused = task_service.pause_claimed_before_work(claimed.token)
    assert paused.status == TaskStatus.PAUSED.value
    assert paused.resume_state == TaskStatus.SCANNING.value
    assert paused.lease_owner is None
    assert task_service.list_checkpoints(queued.id) == []


def test_claimed_worker_honors_termination_before_first_batch(
    task_service: TaskService,
) -> None:
    queued = create_queued(task_service, "terminate startup race")
    claimed = task_service.claim_next(owner="worker", lease_seconds=60)
    assert claimed is not None
    assert (
        task_service.honor_claimed_control_before_work(
            claimed.token,
            phase=TaskStatus.SCANNING,
        )
        is None
    )
    task_service.request_terminate(queued.id)

    terminated = task_service.honor_claimed_control_before_work(
        claimed.token,
        phase=TaskStatus.SCANNING,
    )
    assert terminated is not None
    assert terminated.status == TaskStatus.TERMINATED.value
    assert terminated.resume_state is None
    assert terminated.lease_owner is None
    assert task_service.list_checkpoints(queued.id) == []


def test_stale_lease_recovery_preserves_last_checkpoint(database: Database) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def clock() -> datetime:
        return now

    service = TaskService(database, clock=clock)
    queued = create_queued(service)
    claimed = service.claim_next(owner="worker", lease_seconds=5)
    assert claimed is not None

    now += timedelta(seconds=6)
    recovered = service.recover_stale_leases()
    assert len(recovered) == 1
    assert recovered[0].status == TaskStatus.PAUSED.value
    assert recovered[0].resume_state == TaskStatus.SCANNING.value
    assert recovered[0].error_code == "worker_lease_expired"
    assert service.list_checkpoints(queued.id) == []
    with pytest.raises(StaleWorkerToken):
        service.heartbeat(claimed.token)


def test_phase_completion_and_review_gate(task_service: TaskService) -> None:
    create_queued(task_service)
    for phase in (
        TaskStatus.SCANNING,
        TaskStatus.CPU_METRICS,
        TaskStatus.MODEL_SCORING,
    ):
        claimed = task_service.claim_next(owner=f"worker-{phase.value}", lease_seconds=60)
        assert claimed is not None
        assert claimed.task.status == phase.value
        task = task_service.complete_phase(claimed.token, phase=phase)

    assert task.status == TaskStatus.QUEUED.value
    assert task.resume_state == TaskStatus.STYLE_ANALYSIS.value


def test_config_versions_and_optimistic_control(task_service: TaskService) -> None:
    task = task_service.create_task(
        name="config",
        source_root="E:\\dataset",
        output_root=None,
        config=_task_config(resolutions=(1024,)),
    )
    changed = task_service.update_config(
        task.id,
        _task_config(resolutions=(1216,)),
        expected_version=task.row_version,
    )
    assert changed.current_config_revision == 2
    with pytest.raises(TaskVersionConflict):
        task_service.queue_task(task.id, expected_version=task.row_version)

    restored = task_service.update_config(
        task.id,
        _task_config(resolutions=(1024,)),
        expected_version=changed.row_version,
    )
    assert restored.current_config_revision == 1
    events = task_service.list_events(task.id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


def test_delete_only_allows_terminal_tasks_and_cascades_records(
    database: Database,
    task_service: TaskService,
) -> None:
    draft = task_service.create_task(
        name="delete guard",
        source_root="E:\\dataset",
        output_root="E:\\output",
        config=_task_config(),
    )
    with pytest.raises(InvalidTaskTransition, match="Only completed"):
        task_service.delete_task(draft.id, expected_version=draft.row_version)

    queued = task_service.queue_task(draft.id, expected_version=draft.row_version)
    terminal = task_service.request_terminate(
        queued.id,
        expected_version=queued.row_version,
    )
    assert terminal.status == TaskStatus.TERMINATED.value

    deleted = task_service.delete_task(
        terminal.id,
        expected_version=terminal.row_version,
    )
    assert deleted.id == terminal.id
    with pytest.raises(TaskNotFound):
        task_service.get_task(terminal.id)
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(PhaseCheckpoint)) == 0
