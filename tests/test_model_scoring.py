from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.enums import ReviewState, TaskStatus
from dataset_audit_studio.database.models import (
    Evidence,
    ModelResult,
    ReviewDecision,
    Sample,
    Task,
)
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.main import create_app
from dataset_audit_studio.reviews.service import ReviewService
from dataset_audit_studio.reviews.types import ReviewSelection
from dataset_audit_studio.scoring.assets import verify_runtime_asset_snapshot
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.coordinator import wait_for_runtime_assets
from dataset_audit_studio.scoring.process import run_scoring_subprocess
from dataset_audit_studio.scoring.service import ModelScorer
from dataset_audit_studio.scoring.types import (
    AssetFile,
    ModelAsset,
    RuntimeAssets,
    SampleInput,
    SampleScore,
)
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select


def _asset(root: Path, model_id: str, *paths: str) -> ModelAsset:
    return ModelAsset(
        model_id=model_id,
        loader="test_loader",
        root=str(root / model_id),
        files=tuple(
            AssetFile(
                path=path,
                size=index + 1,
                sha256=hashlib.sha256(f"{model_id}:{path}".encode()).hexdigest(),
                mtime_ns=1,
            )
            for index, path in enumerate(paths)
        ),
        dependencies=(),
        is_custom=False,
        base_model_id=None,
    )


def _assets(tmp_path: Path, *, components: str = "all") -> RuntimeAssets:
    models = [
        _asset(tmp_path, "openai_clip_vit_l14", "ViT-L-14.pt"),
        _asset(tmp_path, "universal_fake_detector_head", "fc_weights.pth"),
        _asset(tmp_path, "community_forensics_model_384", "model.safetensors"),
    ]
    if components == "all":
        models.extend(
            (
                _asset(tmp_path, "aesthetic_lse14_5k", "5kdataset.safetensors"),
                _asset(tmp_path, "jtp3_hydra", "models/jtp-3-hydra.safetensors"),
                _asset(tmp_path, "waifu_scorer_v3", "model.safetensors"),
                _asset(tmp_path, "ppocrv5_server_det", "model.safetensors"),
                _asset(tmp_path, "ppocrv5_server_rec", "model.safetensors"),
                _asset(tmp_path, "watermark_siglip2", "model.safetensors"),
            )
        )
    return RuntimeAssets(models_root=str(tmp_path), models=tuple(models))


def _config(
    *,
    batch_size: int = 2,
    aesthetic: bool = True,
    ai: bool = True,
    ocr: bool = True,
    watermark: bool = True,
) -> dict:
    components = materialize_profile("general")["components"]
    components["feature.clip_l14"]["config"]["batch_size"] = batch_size
    components["score.aesthetic_domain"]["enabled"] = aesthetic
    components["score.aesthetic_domain"]["config"]["in_domain_threshold"] = 0.5
    components["detect.ai"]["enabled"] = ai
    components["detect.ai"]["config"].update(
        {
            "candidate_threshold": 0.35,
            "reference_threshold": 0.5,
        }
    )
    components["evidence.ocr"]["enabled"] = ocr
    components["evidence.ocr"]["config"]["text_density_threshold"] = 0.25
    components["evidence.watermark"]["enabled"] = watermark
    components["evidence.watermark"]["config"]["review_threshold"] = 0.5
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def _with_ai_candidate_threshold(config: dict, threshold: float) -> dict:
    components = copy.deepcopy(config["components"])
    components["detect.ai"]["config"]["candidate_threshold"] = threshold
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def test_legacy_ai_scoring_config_without_model_id_keeps_ufd() -> None:
    config = ScoringConfig.from_task_config({"scoring": {"ai": {"enabled": True}}})

    assert config.ai.model_id == "universal_fake_detector_head"


def _prepare_task(
    database: Database,
    service: TaskService,
    source: Path,
    *,
    config: dict,
    colors: tuple[str, ...] = ("red", "blue"),
) -> tuple[str, tuple[str, ...]]:
    source.mkdir()
    snapshots: list[str] = []
    rows: list[tuple[Path, str, int, int]] = []
    for index, color in enumerate(colors):
        path = source / f"artist-{index % 2}" / f"image-{index}.png"
        path.parent.mkdir(exist_ok=True)
        Image.new("RGB", (64, 64), color).save(path)
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshots.append(digest)
        rows.append((path, digest, stat.st_size, stat.st_mtime_ns))

    task = service.create_task(
        name="model scoring",
        source_root=str(source),
        output_root=None,
        config=config,
    )
    with database.write_session() as session:
        for path, digest, size, mtime_ns in rows:
            session.add(
                Sample(
                    task_id=task.id,
                    relative_path=path.relative_to(source).as_posix(),
                    source_size=size,
                    source_mtime_ns=mtime_ns,
                    source_sha256=digest,
                    pixel_sha256=digest,
                    media_kind="image",
                    artist_scope=path.parent.name,
                    scan_state="valid",
                    encoded_width=64,
                    encoded_height=64,
                    display_width=64,
                    display_height=64,
                    frame_count=1,
                    is_animated=False,
                    exif_orientation=1,
                    extracted_frame_path=None,
                    export_requires_render=False,
                    phash=None,
                    colorhash=None,
                    scan_algorithm_version="test",
                )
            )
    service.queue_task(task.id)
    scanning = service.claim_next(owner="scan", lease_seconds=120)
    assert scanning is not None
    service.complete_phase(scanning.token, phase=TaskStatus.SCANNING)
    cpu = service.claim_next(owner="cpu", lease_seconds=120)
    assert cpu is not None
    service.complete_phase(cpu.token, phase=TaskStatus.CPU_METRICS)
    return task.id, tuple(snapshots)


class _FakeRuntime:
    def __init__(self, *, pause: TaskService | None = None, task_id: str | None = None) -> None:
        self.pause = pause
        self.task_id = task_id
        self.calls = 0
        self.closed = False

    def score_batch(self, samples: tuple[SampleInput, ...]) -> tuple[SampleScore, ...]:
        self.calls += 1
        scores: list[SampleScore] = []
        for sample in samples:
            index = int(Path(sample.relative_path).stem.rsplit("-", 1)[-1])
            scores.append(
                SampleScore(
                    sample_id=sample.sample_id,
                    results={
                        "aesthetic": {
                            "aesthetic": 4.5 - index,
                            "in_domain_prob": 0.8 if index == 0 else 0.2,
                            "in_domain_supported": True,
                        },
                        "ai": {"probability": 0.8 if index == 0 else 0.1},
                        "ocr": {
                            "text_area_ratio": 0.3 if index == 0 else 0.0,
                            "regions": (
                                [
                                    {
                                        "box": [[0, 0], [10, 0], [10, 5], [0, 5]],
                                        "detection_score": 0.9,
                                        "text": "signature",
                                        "recognition_score": 0.8,
                                    }
                                ]
                                if index == 0
                                else []
                            ),
                        },
                        "watermark": {
                            "watermark_probability": 0.7 if index == 0 else 0.2,
                            "probabilities": {
                                "No Watermark": 0.3 if index == 0 else 0.8,
                                "Watermark": 0.7 if index == 0 else 0.2,
                            },
                        },
                    },
                )
            )
        if self.pause is not None and self.calls == 1:
            assert self.task_id is not None
            self.pause.request_pause(self.task_id)
        return tuple(scores)

    def close(self) -> None:
        self.closed = True


class _FakeAIRuntime(_FakeRuntime):
    def score_batch(self, samples: tuple[SampleInput, ...]) -> tuple[SampleScore, ...]:
        self.calls += 1
        scores = tuple(
            SampleScore(
                sample_id=sample.sample_id,
                results={"ai": {"probability": 0.8}},
            )
            for sample in samples
        )
        if self.pause is not None and self.calls == 1:
            assert self.task_id is not None
            self.pause.request_pause(self.task_id)
        return scores


def _claim_scoring(service: TaskService, owner: str):
    claimed = service.claim_next(owner=owner, lease_seconds=120)
    assert claimed is not None
    assert claimed.task.status == TaskStatus.MODEL_SCORING.value
    return claimed


class _DownloadingModels:
    def __init__(self, models_root: Path, on_download) -> None:
        models_root.mkdir(parents=True)
        self.storage = SimpleNamespace(models_root=models_root)
        self.on_download = on_download
        self.downloads: list[tuple[str, bool]] = []

    def get_model(self, model_id: str):
        return SimpleNamespace(
            id=model_id,
            runtime_ready=False,
            installation_status="downloading",
            error=None,
        )

    def download(self, model_id: str, *, include_dependencies: bool = True):
        self.downloads.append((model_id, include_dependencies))
        self.on_download()
        return ()


def _pause_during_asset_download(
    task_service: TaskService,
    tmp_path: Path,
    task_id: str,
    *,
    owner: str,
):
    claimed = _claim_scoring(task_service, owner)
    requested_pause = False

    def pause_once() -> None:
        nonlocal requested_pause
        if not requested_pause:
            requested_pause = True
            task_service.request_pause(task_id)

    models = _DownloadingModels(tmp_path / f"{owner}-models", pause_once)
    assets = wait_for_runtime_assets(
        models,
        task_service,
        claimed.token,
        ScoringConfig.from_task_config({"scoring": {"ai": {"enabled": True}}}),
        poll_seconds=0,
    )
    return assets, models


def test_runtime_asset_snapshot_rejects_changes_after_resolution(tmp_path: Path) -> None:
    models_root = tmp_path / "models"
    model_root = models_root / "test_model"
    model_root.mkdir(parents=True)
    model_file = model_root / "model.safetensors"
    model_file.write_bytes(b"registered")
    stat = model_file.stat()
    assets = RuntimeAssets(
        models_root=str(models_root),
        models=(
            ModelAsset(
                model_id="test_model",
                loader="test_loader",
                root=str(model_root),
                files=(
                    AssetFile(
                        path=model_file.name,
                        size=stat.st_size,
                        sha256=hashlib.sha256(model_file.read_bytes()).hexdigest(),
                        mtime_ns=stat.st_mtime_ns,
                    ),
                ),
                dependencies=(),
                is_custom=False,
                base_model_id=None,
            ),
        ),
    )
    verify_runtime_asset_snapshot(assets)
    model_file.write_bytes(b"changed-after-snapshot")
    with pytest.raises(RuntimeError, match="changed before inference"):
        verify_runtime_asset_snapshot(assets)


def test_pause_during_model_download_commits_asset_wait_checkpoint(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id, _ = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(aesthetic=False, ocr=False, watermark=False),
    )
    assets, models = _pause_during_asset_download(
        task_service,
        tmp_path,
        task_id,
        owner="download-pause",
    )
    assert assets is None
    assert models.downloads == [("universal_fake_detector_head", True)]
    assert task_service.get_task(task_id).status == TaskStatus.PAUSED.value
    checkpoints = task_service.list_checkpoints(
        task_id, phase=TaskStatus.MODEL_SCORING.value
    )
    assert len(checkpoints) == 1
    assert checkpoints[0].batch_index == 0
    assert checkpoints[0].completed_items == 0
    assert checkpoints[0].cursor == {
        "asset_wait": True,
        "next_index": 0,
        "requested_models": ["universal_fake_detector_head"],
    }


def test_pause_between_asset_resolution_and_scoring_process_is_honored(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id, _ = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(aesthetic=False, ocr=False, watermark=False),
    )
    claimed = _claim_scoring(task_service, "startup-pause")
    task_service.request_pause(task_id)

    def should_not_start(_config, _assets):
        raise AssertionError("runtime must not start after a pre-batch pause")

    summary = ModelScorer(task_service, runtime_factory=should_not_start).run(
        claimed.token,
        _assets(tmp_path, components="ai"),
    )
    assert summary.final_status == TaskStatus.PAUSED.value
    assert task_service.get_task(task_id).resume_state == TaskStatus.MODEL_SCORING.value


def test_model_scoring_resumes_after_asset_wait_with_monotonic_batches(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    config = _config(
        batch_size=1,
        aesthetic=False,
        ocr=False,
        watermark=False,
    )
    task_id, _ = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=config,
    )
    assets, _ = _pause_during_asset_download(
        task_service,
        tmp_path,
        task_id,
        owner="asset-wait",
    )
    assert assets is None

    task_service.resume_task(task_id)
    runtime = _FakeAIRuntime()
    summary = ModelScorer(
        task_service,
        runtime_factory=lambda _config, _assets: runtime,
    ).run(
        _claim_scoring(task_service, "asset-resume").token,
        _assets(tmp_path, components="ai"),
    )
    assert summary.resumed_from_index == 0
    assert summary.inferred_samples == 2
    assert summary.final_status == TaskStatus.QUEUED.value
    checkpoints = task_service.list_checkpoints(
        task_id, phase=TaskStatus.MODEL_SCORING.value
    )
    assert [checkpoint.batch_index for checkpoint in checkpoints] == [0, 1, 2]
    assert checkpoints[0].cursor["asset_wait"] is True
    assert all(
        "identity_digest" in checkpoint.cursor for checkpoint in checkpoints[1:]
    )


def test_empty_dataset_completes_in_scoring_subprocess(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id, _ = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(
            aesthetic=False,
            ai=False,
            ocr=False,
            watermark=False,
        ),
        colors=(),
    )
    summary = run_scoring_subprocess(
        database,
        task_service,
        _claim_scoring(task_service, "empty-subprocess").token,
        RuntimeAssets(models_root=str(tmp_path), models=()),
        project_root=tmp_path,
        poll_seconds=0.01,
    )
    assert summary["eligible_samples"] == 0
    assert summary["processed_samples"] == 0
    assert summary["inferred_samples"] == 0
    assert summary["final_status"] == TaskStatus.QUEUED.value


def test_model_scoring_persists_only_agreed_outputs_and_reuses_cache(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    task_id, source_hashes = _prepare_task(database, task_service, source, config=_config())
    runtime = _FakeRuntime()
    first = ModelScorer(
        task_service,
        runtime_factory=lambda _config, _assets: runtime,
    ).run(_claim_scoring(task_service, "score-one").token, _assets(tmp_path))
    assert first.final_status == TaskStatus.QUEUED.value
    assert first.inferred_samples == 2 and first.cached_samples == 0
    assert runtime.closed is True
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(source.rglob("*.png"))
    ] == list(source_hashes)

    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(ModelResult)) == 8
        codes = set(session.scalars(select(Evidence.code)).all())
        assert "aesthetic_score" in codes
        assert "in_domain_probability" in codes
        assert "composition" not in codes
        assert "color" not in codes
        assert "sexual" not in codes
        decisions = session.scalars(
            select(ReviewDecision).where(ReviewDecision.is_active.is_(True))
        ).all()
        assert decisions == []

    changed_config = _with_ai_candidate_threshold(_config(), 0.45)
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.PAUSED.value
        task.resume_state = TaskStatus.MODEL_SCORING.value
    task_service.update_config(task_id, changed_config)
    task_service.resume_task(task_id)

    def cache_miss(*_args):
        raise AssertionError("Unchanged inference identities must use cached model results")

    second = ModelScorer(task_service, runtime_factory=cache_miss).run(
        _claim_scoring(task_service, "score-two").token,
        _assets(tmp_path),
    )
    assert second.inferred_samples == 0 and second.cached_samples == 2
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(ModelResult)) == 8
        ai_thresholds = set(
            session.scalars(
                select(Evidence.threshold_number).where(Evidence.code == "ai_generated_probability")
            ).all()
        )
        assert ai_thresholds == {0.45}


def test_model_scoring_pause_resume_commits_only_complete_batches(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    config = _config(
        batch_size=1,
        aesthetic=False,
        ocr=False,
        watermark=False,
    )
    task_id, _ = _prepare_task(database, task_service, source, config=config)
    pausing_runtime = _FakeAIRuntime(pause=task_service, task_id=task_id)
    paused = ModelScorer(
        task_service,
        runtime_factory=lambda _config, _assets: pausing_runtime,
    ).run(
        _claim_scoring(task_service, "pause-one").token,
        _assets(tmp_path, components="ai"),
    )
    assert paused.processed_samples == 1
    assert paused.final_status == TaskStatus.PAUSED.value

    task_service.resume_task(task_id)
    resumed_runtime = _FakeAIRuntime()
    resumed = ModelScorer(
        task_service,
        runtime_factory=lambda _config, _assets: resumed_runtime,
    ).run(
        _claim_scoring(task_service, "pause-two").token,
        _assets(tmp_path, components="ai"),
    )
    assert resumed.resumed_from_index == 1
    assert resumed.final_status == TaskStatus.QUEUED.value
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(ModelResult)) == 2


def test_ai_review_decisions_are_reversible_and_api_never_modifies_sources(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    task_id, source_hashes = _prepare_task(database, task_service, source, config=_config())
    ModelScorer(
        task_service,
        runtime_factory=lambda _config, _assets: _FakeRuntime(),
    ).run(_claim_scoring(task_service, "review-score").token, _assets(tmp_path))
    with database.read_session() as session:
        ai_sources = set(
            session.scalars(
                select(Evidence.source).where(Evidence.code == "ai_generated_probability")
            ).all()
        )
    assert ai_sources == {"community_forensics"}
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.EVIDENCE_REVIEW.value
        task.resume_state = None

    review = ReviewService(database)
    candidates = review.list_ai_candidates(task_id)
    assert candidates.total == 1 and candidates.pending == 1
    pending_candidates = review.list_ai_candidates(
        task_id,
        decision=ReviewState.PENDING_REVIEW,
    )
    assert pending_candidates.total == 1
    assert pending_candidates.items[0].decision == ReviewState.PENDING_REVIEW.value
    selected = review.decide_ai_candidates(
        task_id,
        selection=ReviewSelection(score_min=0.75),
        decision=ReviewState.APPROVED_EXCLUDE,
    )
    assert selected.selected == 1 and selected.changed == 1
    undone = review.decide_ai_candidates(
        task_id,
        selection=ReviewSelection(sample_ids=(candidates.items[0].sample_id,)),
        decision=ReviewState.APPROVED_KEEP,
    )
    assert undone.changed == 1

    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        models_root=tmp_path / "api-models",
    )
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/reviews/ai")
        assert response.status_code == 200
        assert response.json()["pending"] == 0
        scoped = client.get(f"/api/tasks/{task_id}/reviews/ai?folder=artist-0")
        assert scoped.status_code == 200
        assert scoped.json()["total"] == 1
        empty_scope = client.get(f"/api/tasks/{task_id}/reviews/ai?folder=other-folder")
        assert empty_scope.status_code == 200
        assert empty_scope.json()["total"] == 0
        decision = client.post(
            f"/api/tasks/{task_id}/reviews/ai/decisions",
            json={
                "decision": "approved_exclude",
                "artist_scope": "artist-0",
                "score_min": 0.75,
            },
        )
        assert decision.status_code == 200
        assert decision.json()["changed"] == 1

    with database.read_session() as session:
        history = session.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.category == "ai_generated")
            .order_by(ReviewDecision.created_at, ReviewDecision.id)
        ).all()
        assert len(history) == 3
        assert [item.decision for item in history] == [
            ReviewState.APPROVED_EXCLUDE.value,
            ReviewState.APPROVED_KEEP.value,
            ReviewState.APPROVED_EXCLUDE.value,
        ]
        assert all(item.source == "human" for item in history)
        assert history[1].supersedes_id == history[0].id
        assert history[2].supersedes_id == history[1].id
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(source.rglob("*.png"))
    ] == list(source_hashes)
