from __future__ import annotations

import hashlib
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.app.style_analysis import StyleAnalyzer
from dataset_audit_studio.app.style_process import run_style_subprocess
from dataset_audit_studio.clustering.assets import SIGLIP_MODEL_ID
from dataset_audit_studio.clustering.process import run_clustering_subprocess
from dataset_audit_studio.clustering.service import SemanticClusterer
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import Task
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import InvalidTaskTransition
from dataset_audit_studio.jobs.phase_process import (
    PhaseProcessError,
    run_isolated_phase_subprocess,
)
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.scoring.coordinator import wait_for_model_ids
from dataset_audit_studio.scoring.types import (
    AssetFile,
    ModelAsset,
    RuntimeAssets,
)


def _blocked_phase_entrypoint(_database_path, _token, _payload, _project_root, _result_queue):
    time.sleep(10)


def _large_result_entrypoint(_database_path, _token, _payload, _project_root, result_queue):
    result_queue.put(
        {
            "ok": False,
            "error_type": "LargeChildError",
            "error": "x" * 1_000_000,
            "traceback": "",
        }
    )


class _CompletionRaceTasks:
    def __init__(self, active_status: TaskStatus) -> None:
        self.status = active_status.value
        self.committed_phase: str | None = None

    def get_task(self, _task_id: str):
        return SimpleNamespace(status=self.status)

    def complete_phase(self, _token, *, phase):
        self.status = TaskStatus.PAUSING.value
        raise InvalidTaskTransition(f"pause raced with {phase}")

    def commit_batch(self, _token, *, phase, cursor, **_kwargs):
        assert cursor["control_only"] is True
        self.committed_phase = phase.value
        self.status = TaskStatus.PAUSED.value
        return SimpleNamespace(task=SimpleNamespace(status=self.status))


@pytest.mark.parametrize(
    ("phase", "factory"),
    (
        (
            TaskStatus.STYLE_ANALYSIS,
            lambda tasks, root: StyleAnalyzer(tasks, project_root=root),
        ),
        (
            TaskStatus.SEMANTIC_CLUSTERING,
            lambda tasks, root: SemanticClusterer(tasks, project_root=root),
        ),
    ),
)
def test_stage_f_completion_honors_pause_requested_during_transition(
    tmp_path: Path,
    phase: TaskStatus,
    factory,
) -> None:
    tasks = _CompletionRaceTasks(phase)
    service = factory(tasks, tmp_path)
    status = service._complete_or_control(
        SimpleNamespace(task_id="race-task"),
        "config-hash",
        0,
        0,
        {},
    )
    assert status == TaskStatus.PAUSED.value
    assert tasks.committed_phase == phase.value


def _queue_at_phase(
    database: Database,
    tasks: TaskService,
    source: Path,
    phase: TaskStatus,
) -> str:
    source.mkdir()
    components = materialize_profile("general")["components"]
    task = tasks.create_task(
        name="empty stage f subprocess",
        source_root=str(source),
        output_root=None,
        config=ComponentTaskConfigMaterializer().materialize(
            components,
            profile="general",
            require_profile=True,
        ),
    )
    with database.write_session() as session:
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.QUEUED.value
        row.resume_state = phase.value
    return task.id


def test_isolated_phase_stops_blocked_child_for_pause(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _queue_at_phase(
        database,
        task_service,
        tmp_path / "source",
        TaskStatus.STYLE_ANALYSIS,
    )
    claimed = task_service.claim_next(owner="pause-child", lease_seconds=60)
    assert claimed is not None
    task_service.request_pause(task_id)

    result = run_isolated_phase_subprocess(
        database,
        task_service,
        claimed.token,
        None,
        phase_name="blocked-test",
        entrypoint=_blocked_phase_entrypoint,
        project_root=tmp_path,
        poll_seconds=0.01,
    )

    assert result["status"] == TaskStatus.PAUSED.value
    assert task_service.get_task(task_id).lease_owner is None


def test_isolated_phase_reports_startup_timeout(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _queue_at_phase(
        database,
        task_service,
        tmp_path / "source",
        TaskStatus.STYLE_ANALYSIS,
    )
    claimed = task_service.claim_next(owner="timeout-child", lease_seconds=60)
    assert claimed is not None

    result = run_isolated_phase_subprocess(
        database,
        task_service,
        claimed.token,
        None,
        phase_name="blocked-test",
        entrypoint=_blocked_phase_entrypoint,
        project_root=tmp_path,
        poll_seconds=0.01,
        startup_timeout_seconds=0.1,
    )

    task = task_service.get_task(task_id)
    assert result["status"] == TaskStatus.FAILED.value
    assert task.error_code == "phase_process_startup_timeout"
    assert task.lease_owner is None


def test_isolated_phase_drains_large_result_before_child_exit(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    _queue_at_phase(
        database,
        task_service,
        tmp_path / "source-large-result",
        TaskStatus.STYLE_ANALYSIS,
    )
    claimed = task_service.claim_next(owner="large-result", lease_seconds=60)
    assert claimed is not None

    started = time.monotonic()
    with pytest.raises(PhaseProcessError, match="LargeChildError"):
        run_isolated_phase_subprocess(
            database,
            task_service,
            claimed.token,
            None,
            phase_name="large-result-test",
            entrypoint=_large_result_entrypoint,
            project_root=tmp_path,
            poll_seconds=0.01,
        )

    # Windows spawn cold-imports the entrypoint module; this still guards the 30s join timeout.
    assert time.monotonic() - started < 20.0


def test_empty_style_and_clustering_spawn_processes_reach_evidence_review(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _queue_at_phase(
        database,
        task_service,
        tmp_path / "source",
        TaskStatus.STYLE_ANALYSIS,
    )
    assets = RuntimeAssets(
        models_root=str(tmp_path),
        models=(
            ModelAsset(
                model_id=SIGLIP_MODEL_ID,
                loader="test_loader",
                root=str(tmp_path / SIGLIP_MODEL_ID),
                files=(
                    AssetFile(
                        path="model.safetensors",
                        size=1,
                        sha256=hashlib.sha256(b"empty-clustering").hexdigest(),
                        mtime_ns=1,
                    ),
                ),
                dependencies=(),
                is_custom=False,
                base_model_id=None,
            ),
        ),
    )
    style_claim = task_service.claim_next(owner="style-process", lease_seconds=300)
    assert style_claim is not None
    style = run_style_subprocess(
        database,
        task_service,
        style_claim.token,
        assets,
        project_root=tmp_path,
        poll_seconds=0.01,
    )
    assert style["final_status"] == TaskStatus.QUEUED.value
    assert task_service.get_task(task_id).resume_state == TaskStatus.SEMANTIC_CLUSTERING.value

    cluster_claim = task_service.claim_next(owner="cluster-process", lease_seconds=300)
    assert cluster_claim is not None
    clustering = run_clustering_subprocess(
        database,
        task_service,
        cluster_claim.token,
        assets,
        project_root=tmp_path,
        poll_seconds=0.01,
    )
    assert clustering["final_status"] == TaskStatus.EVIDENCE_REVIEW.value
    assert task_service.get_task(task_id).resume_state is None


class _DownloadingModels:
    def __init__(self, root: Path, tasks: TaskService, task_id: str) -> None:
        root.mkdir()
        self.storage = SimpleNamespace(models_root=root)
        self.tasks = tasks
        self.task_id = task_id
        self.downloads: list[str] = []

    def get_model(self, model_id: str):
        return SimpleNamespace(
            id=model_id,
            runtime_ready=False,
            installation_status="downloading",
            error=None,
        )

    def download(self, model_id: str, *, include_dependencies: bool = True):
        assert include_dependencies is True
        self.downloads.append(model_id)
        self.tasks.request_pause(self.task_id)
        return ()


def _style_assets(root: Path) -> RuntimeAssets:
    def asset(model_id: str, filename: str) -> ModelAsset:
        return ModelAsset(
            model_id=model_id,
            loader="test_loader",
            root=str(root / model_id),
            files=(
                AssetFile(
                    path=filename,
                    size=1,
                    sha256=hashlib.sha256(f"{model_id}:{filename}".encode()).hexdigest(),
                    mtime_ns=1,
                ),
            ),
            dependencies=(),
            is_custom=False,
            base_model_id=None,
        )

    return RuntimeAssets(
        models_root=str(root),
        models=(
            asset("lsnet_kaloscope_v2", "448-90.13/best_checkpoint.pth"),
            asset("vgg19_imagenet1k_v1", "vgg19.pth"),
            asset("dinov2_large", "model.safetensors"),
        ),
    )


def test_style_asset_wait_pause_resumes_without_treating_wait_as_inference(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    components = materialize_profile("artist_concept")["components"]
    task = task_service.create_task(
        name="style asset wait",
        source_root=str(source),
        output_root=None,
        config=ComponentTaskConfigMaterializer().materialize(
            components,
            profile="artist_concept",
            require_profile=True,
        ),
    )
    with database.write_session() as session:
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.QUEUED.value
        row.resume_state = TaskStatus.STYLE_ANALYSIS.value
    claimed = task_service.claim_next(owner="style-assets", lease_seconds=120)
    assert claimed is not None
    models = _DownloadingModels(tmp_path / "models", task_service, task.id)
    result = wait_for_model_ids(
        models,
        task_service,
        claimed.token,
        ("lsnet_kaloscope_v2", "vgg19_imagenet1k_v1", "dinov2_large"),
        phase=TaskStatus.STYLE_ANALYSIS,
        poll_seconds=0,
    )
    assert result is None
    assert task_service.get_task(task.id).status == TaskStatus.PAUSED.value
    checkpoints = task_service.list_checkpoints(
        task.id, phase=TaskStatus.STYLE_ANALYSIS.value
    )
    assert [checkpoint.batch_index for checkpoint in checkpoints] == [0]
    assert checkpoints[0].cursor["asset_wait"] is True

    task_service.resume_task(task.id)
    resumed = task_service.claim_next(owner="style-assets-resume", lease_seconds=120)
    assert resumed is not None
    summary = StyleAnalyzer(
        task_service,
        project_root=tmp_path,
    ).run(resumed.token, _style_assets(tmp_path))
    assert summary.scopes == 0
    assert summary.final_status == TaskStatus.QUEUED.value
    checkpoints = task_service.list_checkpoints(
        task.id, phase=TaskStatus.STYLE_ANALYSIS.value
    )
    assert [checkpoint.batch_index for checkpoint in checkpoints] == [0, 1]
    assert "identity_digest" in checkpoints[1].cursor
