from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

import numpy as np
import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.modular_scoring import (
    ModularScoringComponentService,
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
from dataset_audit_studio.database.models import ModelResult, Sample, Task
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.scoring.config import ScoringConfig
from PIL import Image
from safetensors import safe_open
from safetensors.numpy import save_file
from sqlalchemy import func, select

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
    assert set(clip_checkpoint.cursor["feature_shard"]) == {
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
    clip = _ClipRuntime()
    community = _CommunityAIRuntime()
    service = ModularScoringComponentService(
        task_service,
        runtime_factories={
            "feature.clip_l14": lambda _config, _assets: clip,
            "detect.ai": lambda _config, _assets: community,
        },
        project_root=tmp_path,
    )
    token = _claim(task_service, "community")
    order = ("feature.clip_l14", "detect.ai")
    service.run(token, _assets(tmp_path), component_id=order[0], component_order=order)
    summary = service.run(
        token,
        _assets(tmp_path),
        component_id=order[1],
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
        ("feature.clip_l14", ("openai_clip_vit_l14",)),
        ("detect.ai", ("community_forensics_model_384",)),
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
