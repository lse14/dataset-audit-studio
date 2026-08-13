from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.modular_scoring import (
    ModularScoringComponentService,
    _write_batch_size,
    finalize_modular_scoring,
)
from dataset_audit_studio.app.modular_scoring_coordinator import (
    ModularScoringCoordinator,
    build_scoring_component_plan,
)
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.components.clip_features.runtime import (
    AESTHETIC_FEATURE_CAPABILITY,
    UFD_FEATURE_CAPABILITY,
)
from dataset_audit_studio.core.feature_batch import FeatureBatch
from dataset_audit_studio.core.model_assets import AssetFile, ModelAsset, RuntimeAssets
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import ModelResult, PhaseCheckpoint, Sample, Task
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.errors import StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.scoring.config import ScoringConfig
from PIL import Image
from safetensors import safe_open
from safetensors.numpy import save_file
from sqlalchemy import delete, func, select

ORDER = ("feature.clip_l14", "score.aesthetic_domain", "detect.ai")


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


def _assets(tmp_path: Path) -> RuntimeAssets:
    specs = (
        ("openai_clip_vit_l14", "ViT-L-14.pt"),
        ("aesthetic_lse14_5k", "5kdataset.safetensors"),
        ("jtp3_hydra", "models/jtp-3-hydra.safetensors"),
        ("waifu_scorer_v3", "model.safetensors"),
        ("universal_fake_detector_head", "fc_weights.pth"),
        ("community_forensics_model_384", "model.safetensors"),
        ("ppocrv5_server_det", "model.safetensors"),
        ("ppocrv5_server_rec", "model.safetensors"),
        ("watermark_siglip2", "model.safetensors"),
    )
    return RuntimeAssets(
        models_root=str(tmp_path),
        models=tuple(_asset(tmp_path, model_id, path) for model_id, path in specs),
    )


def _config(*, batch_size: int = 2, jtp_max_sequence: int = 1024) -> dict:
    components = materialize_profile("general")["components"]
    components["feature.clip_l14"]["config"]["batch_size"] = batch_size
    components["score.aesthetic_domain"]["enabled"] = True
    components["score.aesthetic_domain"]["config"][
        "jtp_max_sequence"
    ] = jtp_max_sequence
    components["detect.ai"]["enabled"] = True
    components["detect.ai"]["config"]["model_id"] = "universal_fake_detector_head"
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def _with_aesthetic_sequence(config: dict, jtp_max_sequence: int) -> dict:
    components = copy.deepcopy(config["components"])
    components["score.aesthetic_domain"]["config"][
        "jtp_max_sequence"
    ] = jtp_max_sequence
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def _community_config(*, batch_size: int = 2) -> dict:
    components = materialize_profile("general")["components"]
    components["feature.clip_l14"]["config"]["batch_size"] = batch_size
    components["detect.ai"]["enabled"] = True
    components["detect.ai"]["config"]["model_id"] = "community_forensics_model_384"
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def _component_config(
    *component_ids: str,
    batch_size: int = 2,
    ai_model_id: str = "universal_fake_detector_head",
) -> dict:
    components = materialize_profile("general")["components"]
    for component_id in (
        "score.aesthetic_domain",
        "detect.ai",
        "evidence.ocr",
        "evidence.watermark",
    ):
        components[component_id]["enabled"] = component_id in component_ids
    components["feature.clip_l14"]["config"]["batch_size"] = batch_size
    components["detect.ai"]["config"]["model_id"] = ai_model_id
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def _component_order(component_id: str, *, ai_model_id: str) -> tuple[str, ...]:
    if component_id == "score.aesthetic_domain" or (
        component_id == "detect.ai" and ai_model_id == "universal_fake_detector_head"
    ):
        return ("feature.clip_l14", component_id)
    return (component_id,)


def _prepare_task(
    database: Database,
    tasks: TaskService,
    source: Path,
    *,
    config: dict,
    colors: tuple[str, ...] = ("red", "blue"),
) -> str:
    source.mkdir()
    rows = []
    for index, color in enumerate(colors):
        path = source / f"artist-{index}" / f"image-{index}.png"
        path.parent.mkdir()
        Image.new("RGB", (32, 32), color).save(path)
        stat = path.stat()
        rows.append((path, hashlib.sha256(path.read_bytes()).hexdigest(), stat))
    task = tasks.create_task(
        name="modular scoring",
        source_root=str(source),
        output_root=None,
        config=config,
    )
    with database.write_session() as session:
        for path, digest, stat in rows:
            session.add(
                Sample(
                    task_id=task.id,
                    relative_path=path.relative_to(source).as_posix(),
                    source_size=stat.st_size,
                    source_mtime_ns=stat.st_mtime_ns,
                    source_sha256=digest,
                    pixel_sha256=digest,
                    media_kind="image",
                    artist_scope=path.parent.name,
                    scan_state="valid",
                    encoded_width=32,
                    encoded_height=32,
                    display_width=32,
                    display_height=32,
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
    tasks.queue_task(task.id)
    scan = tasks.claim_next(owner="scan", lease_seconds=120)
    assert scan is not None
    tasks.complete_phase(scan.token, phase=TaskStatus.SCANNING)
    metrics = tasks.claim_next(owner="metrics", lease_seconds=120)
    assert metrics is not None
    tasks.complete_phase(metrics.token, phase=TaskStatus.CPU_METRICS)
    return task.id


def _claim(tasks: TaskService, owner: str):
    claimed = tasks.claim_next(owner=owner, lease_seconds=120)
    assert claimed is not None
    assert claimed.task.status == TaskStatus.MODEL_SCORING.value
    return claimed.token


class _ClipRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False
        self.capabilities: list[tuple[str, ...]] = []

    def extract(self, images, sample_ids, capabilities) -> FeatureBatch:
        self.calls += 1
        self.capabilities.append(capabilities)
        assert len(images) == len(sample_ids)
        features = {
            capability: np.full(
                (len(sample_ids), 3),
                11.0 if capability == AESTHETIC_FEATURE_CAPABILITY else 22.0,
                dtype=np.float32,
            )
            for capability in capabilities
        }
        return FeatureBatch.create(sample_ids, features)

    def close(self) -> None:
        self.closed = True


class _AestheticRuntime:
    def __init__(self, *, pause: TaskService | None = None, task_id: str | None = None) -> None:
        self.calls = 0
        self.closed = False
        self.pause = pause
        self.task_id = task_id

    def score(self, images, features):
        self.calls += 1
        assert len(images) == len(features)
        assert np.all(features == 11.0)
        if self.pause is not None and self.calls == 1:
            assert self.task_id is not None
            self.pause.request_pause(self.task_id)
        return [
            {
                "aesthetic": 4.0,
                "in_domain_prob": 0.8,
                "in_domain_supported": True,
            }
            for _ in images
        ]

    def close(self) -> None:
        self.closed = True


class _AIRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def score(self, features):
        self.calls += 1
        assert np.all(features == 22.0)
        return [{"probability": 0.1} for _ in features]

    def close(self) -> None:
        self.closed = True


class _CommunityAIRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def score(self, images):
        self.calls += 1
        assert all(isinstance(image, Image.Image) for image in images)
        return [{"probability": 0.8} for _ in images]

    def close(self) -> None:
        self.closed = True


class _ComponentRuntime:
    def __init__(self, component_id: str, *, non_finite_call: int | None = None) -> None:
        self.component_id = component_id
        self.non_finite_call = non_finite_call
        self.calls = 0
        self.closed = False

    def score(self, *inputs):
        self.calls += 1
        count = len(inputs[0])
        if self.non_finite_call == self.calls:
            return [{"probability": float("nan")} for _ in range(count)]
        if self.component_id == "score.aesthetic_domain":
            return [
                {"aesthetic": 4.0, "in_domain_prob": 0.8, "in_domain_supported": True}
                for _ in range(count)
            ]
        if self.component_id == "detect.ai":
            return [{"probability": 0.1} for _ in range(count)]
        if self.component_id == "evidence.ocr":
            return [{"regions": [], "text_area_ratio": 0.0} for _ in range(count)]
        if self.component_id == "evidence.watermark":
            return [
                {"watermark_probability": 0.1, "probabilities": {"No Watermark": 0.9}}
                for _ in range(count)
            ]
        raise AssertionError(f"unsupported component: {self.component_id}")

    def close(self) -> None:
        self.closed = True


def _factories(clip, aesthetic, ai):
    return {
        "feature.clip_l14": lambda _config, _assets: clip,
        "score.aesthetic_domain": lambda _config, _assets: aesthetic,
        "detect.ai": lambda _config, _assets: ai,
        "evidence.ocr": lambda _config, _assets: pytest.fail("OCR must remain unloaded"),
        "evidence.watermark": lambda _config, _assets: pytest.fail(
            "watermark must remain unloaded"
        ),
    }


def test_components_exchange_capabilities_and_checkpoint_independently(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(),
    )
    clip, aesthetic, ai = _ClipRuntime(), _AestheticRuntime(), _AIRuntime()
    service = ModularScoringComponentService(
        task_service,
        runtime_factories=_factories(clip, aesthetic, ai),
        project_root=tmp_path,
    )
    token = _claim(task_service, "modular")
    summaries = [
        service.run(token, _assets(tmp_path), component_id=component, component_order=ORDER)
        for component in ORDER
    ]
    assert all(summary.component_complete for summary in summaries)
    assert finalize_modular_scoring(task_service, token, component_order=ORDER) == (
        TaskStatus.QUEUED.value
    )
    assert clip.calls == aesthetic.calls == ai.calls == 1
    assert clip.closed and aesthetic.closed and ai.closed
    assert clip.capabilities == [
        tuple(sorted((AESTHETIC_FEATURE_CAPABILITY, UFD_FEATURE_CAPABILITY)))
    ]

    checkpoints = task_service.list_checkpoints(
        task_id, phase=TaskStatus.MODEL_SCORING.value
    )
    by_component = {checkpoint.cursor["component_id"] for checkpoint in checkpoints}
    assert by_component == set(ORDER)
    clip_checkpoint = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.cursor["component_id"] == "feature.clip_l14"
    )
    assert set(clip_checkpoint.cursor["feature_shards"][0]) == {
        "cache_key",
        "relative_path",
        "sha256",
    }
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(ModelResult)) == 4


def test_community_ai_component_scores_decoded_images_not_clip_features(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_community_config(),
    )
    community = _CommunityAIRuntime()
    service = ModularScoringComponentService(
        task_service,
        runtime_factories={
            "feature.clip_l14": lambda *_args: pytest.fail(
                "CF-only scoring must not load CLIP"
            ),
            "detect.ai": lambda _config, _assets: community,
        },
        project_root=tmp_path,
    )
    token = _claim(task_service, "community")
    order = ("detect.ai",)
    summary = service.run(
        token,
        _assets(tmp_path),
        component_id=order[0],
        component_order=order,
    )

    assert summary.component_complete is True
    assert community.calls == 1
    assert community.closed is True


def test_only_changed_component_loses_its_cache(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    original = _config()
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=original,
    )
    first = ModularScoringComponentService(
        task_service,
        runtime_factories=_factories(_ClipRuntime(), _AestheticRuntime(), _AIRuntime()),
        project_root=tmp_path,
    )
    token = _claim(task_service, "first")
    for component in ORDER:
        first.run(token, _assets(tmp_path), component_id=component, component_order=ORDER)
    finalize_modular_scoring(task_service, token, component_order=ORDER)

    changed = _with_aesthetic_sequence(original, 512)
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.PAUSED.value
        task.resume_state = TaskStatus.MODEL_SCORING.value
    task_service.update_config(task_id, changed)
    task_service.resume_task(task_id)

    aesthetic = _AestheticRuntime()
    second = ModularScoringComponentService(
        task_service,
        runtime_factories={
            "feature.clip_l14": lambda *_args: pytest.fail("CLIP cache must be reused"),
            "score.aesthetic_domain": lambda _config, _assets: aesthetic,
            "detect.ai": lambda *_args: pytest.fail("AI cache must be reused"),
        },
        project_root=tmp_path,
    )
    token = _claim(task_service, "second")
    summaries = {
        component: second.run(
            token,
            _assets(tmp_path),
            component_id=component,
            component_order=ORDER,
        )
        for component in ORDER
    }
    assert summaries["feature.clip_l14"].cached_samples == 2
    assert summaries["score.aesthetic_domain"].inferred_samples == 2
    assert summaries["detect.ai"].cached_samples == 2
    assert aesthetic.calls == 1


def test_pause_resume_does_not_repeat_committed_component_batches(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(batch_size=1),
    )
    clip = _ClipRuntime()
    pausing = _AestheticRuntime(pause=task_service, task_id=task_id)
    service = ModularScoringComponentService(
        task_service,
        runtime_factories=_factories(clip, pausing, _AIRuntime()),
        project_root=tmp_path,
    )
    token = _claim(task_service, "pause")
    service.run(token, _assets(tmp_path), component_id=ORDER[0], component_order=ORDER)
    paused = service.run(
        token,
        _assets(tmp_path),
        component_id=ORDER[1],
        component_order=ORDER,
    )
    assert paused.processed_samples == 1
    assert paused.final_status == TaskStatus.PAUSED.value
    assert pausing.calls == 1

    task_service.resume_task(task_id)
    resumed_runtime = _AestheticRuntime()
    resumed_service = ModularScoringComponentService(
        task_service,
        runtime_factories={
            "score.aesthetic_domain": lambda _config, _assets: resumed_runtime,
        },
        project_root=tmp_path,
    )
    resumed = resumed_service.run(
        _claim(task_service, "resume"),
        _assets(tmp_path),
        component_id=ORDER[1],
        component_order=ORDER,
    )
    assert resumed.resumed_from_index == 1
    assert resumed.inferred_samples == 2
    assert resumed_runtime.calls == 1


def test_consumer_rejects_clip_shard_changed_after_checkpoint(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(),
    )
    service = ModularScoringComponentService(
        task_service,
        runtime_factories=_factories(_ClipRuntime(), _AestheticRuntime(), _AIRuntime()),
        project_root=tmp_path,
    )
    token = _claim(task_service, "tamper")
    service.run(token, _assets(tmp_path), component_id=ORDER[0], component_order=ORDER)
    path = next(tmp_path.rglob("*.safetensors"))
    with safe_open(str(path), framework="np") as handle:
        metadata = dict(handle.metadata() or {})
        names = tuple(handle.keys())
        tensors = {name: handle.get_tensor(name) for name in names}
    first_capability = sorted(tensors)[0]
    tensors[first_capability][0, 0] += 1.0
    save_file(tensors, str(path), metadata=metadata)

    with pytest.raises(RuntimeError, match="changed after its checkpoint"):
        service.run(
            token,
            _assets(tmp_path),
            component_id=ORDER[1],
            component_order=ORDER,
        )


def test_clip_checkpoint_registers_every_inference_shard_in_one_write_batch(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(batch_size=2),
        colors=("red", "blue", "green", "purple", "orange"),
    )
    clip, aesthetic, ai = _ClipRuntime(), _AestheticRuntime(), _AIRuntime()
    service = ModularScoringComponentService(
        task_service,
        runtime_factories=_factories(clip, aesthetic, ai),
        project_root=tmp_path,
    )
    token = _claim(task_service, "clip-shards")

    clip_summary = service.run(
        token,
        _assets(tmp_path),
        component_id=ORDER[0],
        component_order=ORDER,
    )
    service.run(token, _assets(tmp_path), component_id=ORDER[1], component_order=ORDER)
    service.run(token, _assets(tmp_path), component_id=ORDER[2], component_order=ORDER)

    checkpoint = next(
        item
        for item in task_service.list_checkpoints(task_id, phase=TaskStatus.MODEL_SCORING.value)
        if item.cursor.get("component_id") == ORDER[0]
    )
    descriptors = checkpoint.cursor["feature_shards"]
    assert clip_summary.processed_samples == 5
    assert clip.calls == aesthetic.calls == ai.calls == 3
    assert len(descriptors) == 3
    assert len({item["cache_key"] for item in descriptors}) == 3
    assert "feature_shard" not in checkpoint.cursor


def test_clip_shard_reader_accepts_legacy_and_aggregated_checkpoint_descriptors() -> None:
    legacy = {"cache_key": "legacy", "relative_path": "legacy.safetensors", "sha256": "a"}
    grouped = {"cache_key": "grouped", "relative_path": "grouped.safetensors", "sha256": "b"}
    checkpoints = [
        SimpleNamespace(
            cursor={
                "modular_scoring": True,
                "component_id": "feature.clip_l14",
                "feature_shard": legacy,
            }
        ),
        SimpleNamespace(
            cursor={
                "modular_scoring": True,
                "component_id": "feature.clip_l14",
                "feature_shards": [grouped],
            }
        ),
    ]

    assert ModularScoringComponentService._registered_clip_shard(checkpoints, "legacy") == (
        "a",
        "legacy.safetensors",
    )
    assert ModularScoringComponentService._registered_clip_shard(checkpoints, "grouped") == (
        "b",
        "grouped.safetensors",
    )


def test_non_clip_component_groups_inference_batches_into_one_atomic_write(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(batch_size=2),
        colors=("red", "blue", "green", "purple", "orange"),
    )
    clip, aesthetic, ai = _ClipRuntime(), _AestheticRuntime(), _AIRuntime()
    service = ModularScoringComponentService(
        task_service,
        runtime_factories=_factories(clip, aesthetic, ai),
        project_root=tmp_path,
    )
    token = _claim(task_service, "aggregate-aesthetic")
    service.run(token, _assets(tmp_path), component_id=ORDER[0], component_order=ORDER)
    commits: list[dict[str, object]] = []
    original_commit = task_service.commit_batch

    def record_commit(*args, **kwargs):
        commits.append(dict(kwargs))
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(task_service, "commit_batch", record_commit)
    summary = service.run(token, _assets(tmp_path), component_id=ORDER[1], component_order=ORDER)

    data_commits = [item for item in commits if item.get("batch_writer") is not None]
    assert summary.processed_samples == 5
    assert summary.inferred_samples == 5
    assert aesthetic.calls == 3
    assert len(data_commits) == 1
    assert data_commits[0]["cursor"]["next_index"] == 5
    checkpoint = next(
        item
        for item in task_service.list_checkpoints(task_id, phase=TaskStatus.MODEL_SCORING.value)
        if item.cursor.get("component_id") == ORDER[1]
    )
    assert checkpoint.cursor["results_prepared"] is True
    assert checkpoint.cursor["component_complete"] is True


@pytest.mark.parametrize(
    ("component_id", "ai_model_id"),
    [
        ("score.aesthetic_domain", "universal_fake_detector_head"),
        ("detect.ai", "universal_fake_detector_head"),
        ("detect.ai", "community_forensics_model_384"),
        ("evidence.ocr", "universal_fake_detector_head"),
        ("evidence.watermark", "universal_fake_detector_head"),
    ],
    ids=["aesthetic", "ufd_ai", "community_ai", "ocr", "watermark"],
)
def test_non_clip_components_aggregate_complete_inference_batches(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch,
    component_id: str,
    ai_model_id: str,
) -> None:
    order = _component_order(component_id, ai_model_id=ai_model_id)
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_component_config(component_id, ai_model_id=ai_model_id),
        colors=("red", "blue", "green", "purple", "orange"),
    )
    runtime = _ComponentRuntime(component_id)
    clip = _ClipRuntime()
    service = ModularScoringComponentService(
        task_service,
        runtime_factories={
            "feature.clip_l14": lambda _config, _assets: clip,
            component_id: lambda _config, _assets: runtime,
        },
        project_root=tmp_path,
    )
    token = _claim(task_service, f"aggregate-{component_id}")
    if order[0] == "feature.clip_l14":
        service.run(token, _assets(tmp_path), component_id=order[0], component_order=order)
    commits: list[dict[str, object]] = []
    original_commit = task_service.commit_batch

    def record_commit(*args, **kwargs):
        commits.append(dict(kwargs))
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(task_service, "commit_batch", record_commit)
    summary = service.run(
        token,
        _assets(tmp_path),
        component_id=component_id,
        component_order=order,
    )

    data_commits = [item for item in commits if item.get("batch_writer") is not None]
    checkpoint = next(
        item
        for item in task_service.list_checkpoints(task_id, phase=TaskStatus.MODEL_SCORING.value)
        if item.cursor.get("component_id") == component_id
    )
    assert runtime.calls == 3
    assert len(data_commits) == 1
    assert summary.inferred_samples == 5
    assert summary.cached_samples == 0
    assert checkpoint.cursor["next_index"] == 5
    assert checkpoint.cursor["results_prepared"] is True
    assert checkpoint.cursor["component_complete"] is True


def test_component_cache_hits_join_pending_scores_with_inferred_batches(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_component_config("detect.ai", ai_model_id="community_forensics_model_384"),
        colors=("red", "blue", "green", "purple", "orange"),
    )
    runtime = _ComponentRuntime("detect.ai")
    service = ModularScoringComponentService(
        task_service,
        runtime_factories={"detect.ai": lambda _config, _assets: runtime},
        project_root=tmp_path,
    )
    token = _claim(task_service, "cache-seed")
    service.run(token, _assets(tmp_path), component_id="detect.ai", component_order=("detect.ai",))
    with database.write_session() as session:
        session.execute(
            delete(ModelResult)
            .where(ModelResult.task_id == task_id)
            .where(ModelResult.sample_id.in_(
                select(Sample.id)
                .where(Sample.task_id == task_id)
                .order_by(Sample.relative_path, Sample.id)
                .limit(2)
            ))
        )
        session.execute(
            delete(PhaseCheckpoint).where(
                PhaseCheckpoint.task_id == task_id,
                PhaseCheckpoint.phase == TaskStatus.MODEL_SCORING.value,
            )
        )
    runtime = _ComponentRuntime("detect.ai")
    service = ModularScoringComponentService(
        task_service,
        runtime_factories={"detect.ai": lambda _config, _assets: runtime},
        project_root=tmp_path,
    )
    commits: list[dict[str, object]] = []
    original_commit = task_service.commit_batch

    def record_commit(*args, **kwargs):
        commits.append(dict(kwargs))
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(task_service, "commit_batch", record_commit)
    summary = service.run(
        token,
        _assets(tmp_path),
        component_id="detect.ai",
        component_order=("detect.ai",),
    )

    checkpoint = next(
        item
        for item in task_service.list_checkpoints(task_id, phase=TaskStatus.MODEL_SCORING.value)
        if item.cursor.get("component_id") == "detect.ai" and item.cursor["component_complete"]
    )
    assert runtime.calls == 1
    assert summary.inferred_samples == 2
    assert summary.cached_samples == 3
    assert len([item for item in commits if item.get("batch_writer") is not None]) == 1
    assert checkpoint.cursor["next_index"] == 5


def test_non_finite_later_component_batch_does_not_enter_pending_persistence(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_component_config("detect.ai", ai_model_id="community_forensics_model_384"),
        colors=("red", "blue", "green", "purple", "orange"),
    )
    runtime = _ComponentRuntime("detect.ai", non_finite_call=2)
    service = ModularScoringComponentService(
        task_service,
        runtime_factories={"detect.ai": lambda _config, _assets: runtime},
        project_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="Non-finite component output"):
        service.run(
            _claim(task_service, "non-finite"),
            _assets(tmp_path),
            component_id="detect.ai",
            component_order=("detect.ai",),
        )

    assert task_service.list_checkpoints(task_id, phase=TaskStatus.MODEL_SCORING.value) == []
    with database.read_session() as session:
        assert session.scalar(
            select(func.count()).select_from(ModelResult).where(ModelResult.task_id == task_id)
        ) == 0


def test_stale_scoring_commit_discards_pending_scores_and_returns_committed_prefix(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_component_config("detect.ai", ai_model_id="community_forensics_model_384"),
        colors=("red", "blue"),
    )
    service = ModularScoringComponentService(
        task_service,
        runtime_factories={"detect.ai": lambda _config, _assets: _ComponentRuntime("detect.ai")},
        project_root=tmp_path,
    )

    def stale_commit(*_args, **_kwargs):
        raise StaleWorkerToken("injected stale scoring commit")

    monkeypatch.setattr(task_service, "commit_batch", stale_commit)
    summary = service.run(
        _claim(task_service, "stale-scoring"),
        _assets(tmp_path),
        component_id="detect.ai",
        component_order=("detect.ai",),
    )

    assert summary.processed_samples == 0
    assert summary.inferred_samples == 0
    assert summary.cached_samples == 0
    assert summary.component_complete is False
    with database.read_session() as session:
        assert session.scalar(
            select(func.count()).select_from(ModelResult).where(ModelResult.task_id == task_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(PhaseCheckpoint)
            .where(PhaseCheckpoint.task_id == task_id)
        ) == 0


def test_prepare_runs_once_and_later_component_preserves_prepared_results(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _component_config("evidence.ocr", "evidence.watermark")
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=config,
        colors=("red", "blue", "green", "purple", "orange"),
    )
    ocr = _ComponentRuntime("evidence.ocr")
    watermark = _ComponentRuntime("evidence.watermark")
    service = ModularScoringComponentService(
        task_service,
        runtime_factories={
            "evidence.ocr": lambda _config, _assets: ocr,
            "evidence.watermark": lambda _config, _assets: watermark,
        },
        project_root=tmp_path,
    )
    prepares: list[bool] = []
    original_persist = service.repository.persist_batch

    def record_persist(session, **kwargs):
        prepares.append(kwargs["prepare"])
        return original_persist(session, **kwargs)

    monkeypatch.setattr(service.repository, "persist_batch", record_persist)
    token = _claim(task_service, "prepare-once")
    order = ("evidence.ocr", "evidence.watermark")
    service.run(token, _assets(tmp_path), component_id=order[0], component_order=order)
    service.run(token, _assets(tmp_path), component_id=order[1], component_order=order)

    checkpoints = {
        item.cursor["component_id"]: item
        for item in task_service.list_checkpoints(task_id, phase=TaskStatus.MODEL_SCORING.value)
    }
    assert prepares == [True, False]
    assert checkpoints["evidence.ocr"].cursor["results_prepared"] is True
    assert checkpoints["evidence.watermark"].cursor["results_prepared"] is True
    assert checkpoints["evidence.watermark"].cursor["component_complete"] is True


@pytest.mark.parametrize(
    ("total", "inference_batch", "expected_writes"),
    [(100_000, 1, 1563), (100_000, 256, 391)],
    ids=["batch1_target64", "batch256_target64"],
)
def test_100k_write_batch_schedule_has_deterministic_transaction_count(
    total: int,
    inference_batch: int,
    expected_writes: int,
) -> None:
    flush_size = _write_batch_size(inference_batch, target=64)

    assert (total + flush_size - 1) // flush_size == expected_writes


def test_scoring_config_used_by_fakes_matches_component_contract() -> None:
    config = ScoringConfig.from_task_config(_config())
    assert config.enabled_components == ("aesthetic", "ai")


def test_registry_plan_contains_only_enabled_component_models() -> None:
    plan = build_scoring_component_plan(_config())
    assert [(item.component_id, item.model_ids) for item in plan] == [
        ("feature.clip_l14", ("openai_clip_vit_l14",)),
        (
            "score.aesthetic_domain",
            ("aesthetic_lse14_5k", "jtp3_hydra", "waifu_scorer_v3"),
        ),
        ("detect.ai", ("universal_fake_detector_head",)),
    ]


def test_community_registry_plan_requests_only_the_community_model() -> None:
    plan = build_scoring_component_plan(_community_config())

    assert [(item.component_id, item.model_ids) for item in plan] == [
        ("detect.ai", ("community_forensics_model_384",)),
    ]
    assert all("openai_clip_vit_l14" not in item.model_ids for item in plan)


def test_community_registry_plan_keeps_explicit_clip_component() -> None:
    config = _community_config()
    config["components"]["feature.clip_l14"]["enabled"] = True

    plan = build_scoring_component_plan(config)

    assert [(item.component_id, item.model_ids) for item in plan] == [
        ("feature.clip_l14", ("openai_clip_vit_l14",)),
        ("detect.ai", ("community_forensics_model_384",)),
    ]


def test_community_ai_does_not_request_clip_capabilities() -> None:
    config = ScoringConfig.from_task_config(_community_config())

    assert config.enabled_components == ("ai",)
    assert config.ai.model_id == "community_forensics_model_384"
    assert ModularScoringComponentService._clip_capabilities(config) == ()


def test_community_coordinator_does_not_wait_for_clip(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_community_config(),
        colors=(),
    )
    requested: list[tuple[str, tuple[str, ...]]] = []

    def wait_for_assets(_token, item):
        requested.append((item.component_id, item.model_ids))
        return _assets(tmp_path)

    summary = ModularScoringCoordinator(
        database,
        task_service,
        model_service=None,
        component_asset_waiter=wait_for_assets,
        project_root=tmp_path,
        poll_seconds=0.01,
    ).run(_claim(task_service, "community-coordinator"))

    assert summary.final_status == TaskStatus.QUEUED.value
    assert requested == [("detect.ai", ("community_forensics_model_384",))]
    assert [item["runtime_model_ids"] for item in summary.component_summaries] == [
        ["community_forensics_model_384"]
    ]


def test_coordinator_spawns_each_component_with_only_its_models(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(),
        colors=(),
    )
    resolved_assets = _assets(tmp_path)
    requested: list[tuple[str, tuple[str, ...]]] = []

    def wait_for_assets(_token, item):
        requested.append((item.component_id, item.model_ids))
        return resolved_assets

    summary = ModularScoringCoordinator(
        database,
        task_service,
        model_service=None,
        component_asset_waiter=wait_for_assets,
        project_root=tmp_path,
        poll_seconds=0.01,
    ).run(_claim(task_service, "coordinator"))

    assert summary.final_status == TaskStatus.QUEUED.value
    assert requested == [
        ("feature.clip_l14", ("openai_clip_vit_l14",)),
        (
            "score.aesthetic_domain",
            ("aesthetic_lse14_5k", "jtp3_hydra", "waifu_scorer_v3"),
        ),
        ("detect.ai", ("universal_fake_detector_head",)),
    ]
    assert [item["runtime_model_ids"] for item in summary.component_summaries] == [
        ["openai_clip_vit_l14"],
        ["aesthetic_lse14_5k", "jtp3_hydra", "waifu_scorer_v3"],
        ["universal_fake_detector_head"],
    ]
    assert all(
        item["process_pid"] != os.getpid() for item in summary.component_summaries
    )
    assert [item["component_id"] for item in summary.component_summaries] == list(ORDER)
    checkpoints = task_service.list_checkpoints(
        task_id, phase=TaskStatus.MODEL_SCORING.value
    )
    assert {item.cursor["component_id"] for item in checkpoints} == set(ORDER)
