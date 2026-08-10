from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import numpy as np
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.app.style_analysis import StyleAnalyzer
from dataset_audit_studio.database.enums import TaskStatus
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
from dataset_audit_studio.scoring.service import ModelScorer
from dataset_audit_studio.scoring.types import (
    AssetFile,
    ModelAsset,
    RuntimeAssets,
)
from dataset_audit_studio.style.repository import STYLE_EVIDENCE_SOURCE
from dataset_audit_studio.style.types import StyleFeatureBatch, StyleSample
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select


def _asset(root: Path, model_id: str, path: str) -> ModelAsset:
    return ModelAsset(
        model_id=model_id,
        loader="test_loader",
        root=str(root / model_id),
        files=(
            AssetFile(
                path=path,
                size=1,
                sha256=hashlib.sha256(f"{model_id}:{path}".encode()).hexdigest(),
                mtime_ns=1,
            ),
        ),
        dependencies=(),
        is_custom=False,
        base_model_id=None,
    )


def _style_assets(tmp_path: Path) -> RuntimeAssets:
    return RuntimeAssets(
        models_root=str(tmp_path),
        models=(
            _asset(tmp_path, "lsnet_kaloscope_v2", "448-90.13/best_checkpoint.pth"),
            _asset(tmp_path, "vgg19_imagenet1k_v1", "vgg19-dcbb9e9d.pth"),
            _asset(tmp_path, "dinov2_large", "model.safetensors"),
        ),
    )


def _style_config(*, batch_size: int) -> dict:
    components = materialize_profile("artist_concept")["components"]
    components["style.artist"]["config"]["batch_size"] = batch_size
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="artist_concept",
        require_profile=True,
    )


def _prepare_style_task(
    database: Database,
    tasks: TaskService,
    source: Path,
    *,
    config: dict,
) -> tuple[str, tuple[str, ...]]:
    source.mkdir()
    source_hashes: list[str] = []
    rows: list[tuple[Path, str, int, int]] = []
    for artist, count in (("artist-a", 8), ("artist-b", 3)):
        for index in range(count):
            path = source / artist / f"image-{index}.png"
            path.parent.mkdir(exist_ok=True)
            Image.new("RGB", (64, 64), (index * 20, 40, 80)).save(path)
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            source_hashes.append(digest)
            rows.append((path, digest, stat.st_size, stat.st_mtime_ns))
    task = tasks.create_task(
        name="style analysis",
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
    tasks.queue_task(task.id)
    for owner, phase in (
        ("scan", TaskStatus.SCANNING),
        ("cpu", TaskStatus.CPU_METRICS),
    ):
        claimed = tasks.claim_next(owner=owner, lease_seconds=120)
        assert claimed is not None and claimed.task.status == phase.value
        tasks.complete_phase(claimed.token, phase=phase)
    scoring = tasks.claim_next(owner="score", lease_seconds=120)
    assert scoring is not None
    ModelScorer(tasks).run(
        scoring.token,
        RuntimeAssets(models_root=str(source), models=()),
    )
    current = tasks.get_task(task.id)
    assert current.status == TaskStatus.QUEUED.value
    assert current.resume_state == TaskStatus.STYLE_ANALYSIS.value
    return task.id, tuple(source_hashes)


def _claim_style(tasks: TaskService, owner: str):
    claimed = tasks.claim_next(owner=owner, lease_seconds=120)
    assert claimed is not None
    assert claimed.task.status == TaskStatus.STYLE_ANALYSIS.value
    return claimed


class _FakeStyleRuntime:
    def __init__(
        self,
        *,
        pause: TaskService | None = None,
        task_id: str | None = None,
    ) -> None:
        self.pause = pause
        self.task_id = task_id
        self.calls = 0
        self.closed = False

    def extract(self, samples: tuple[StyleSample, ...]) -> StyleFeatureBatch:
        self.calls += 1
        lsnet = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(samples), 1))
        gram = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(samples), 1))
        dino = gram.copy()
        colors = np.tile(
            np.array([[0.5, 0.5]], dtype=np.float32),
            (len(samples), 1),
        )
        for row, sample in enumerate(samples):
            if sample.artist_scope == "artist-a" and sample.relative_path.endswith(
                "image-7.png"
            ):
                lsnet[row] = (-1.0, 0.0)
                gram[row] = (-1.0, 0.0)
        if self.pause is not None and self.calls == 1:
            assert self.task_id is not None
            self.pause.request_pause(self.task_id)
        return StyleFeatureBatch(
            sample_ids=tuple(sample.sample_id for sample in samples),
            lsnet=lsnet,
            gram=gram,
            dino=dino,
            color_histogram=colors,
        )

    def close(self) -> None:
        self.closed = True


def test_style_service_groups_artists_persists_review_and_reuses_scope_cache(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    config = _style_config(batch_size=4)
    task_id, source_hashes = _prepare_style_task(
        database,
        task_service,
        tmp_path / "source",
        config=config,
    )
    project = tmp_path / "project"
    project.mkdir()
    runtime = _FakeStyleRuntime()
    first = StyleAnalyzer(
        task_service,
        runtime_factory=lambda _config, _assets: runtime,
        project_root=project,
    ).run(
        _claim_style(task_service, "style-one").token,
        _style_assets(tmp_path),
    )
    assert first.scopes == 2
    assert first.inferred_samples == 11 and first.cached_samples == 0
    assert first.final_status == TaskStatus.QUEUED.value
    assert runtime.closed is True
    assert task_service.get_task(task_id).resume_state == TaskStatus.SEMANTIC_CLUSTERING.value

    with database.read_session() as session:
        assert session.scalar(
            select(func.count()).select_from(ModelResult).where(
                ModelResult.model_id == "artist_style_lsnet_v2"
            )
        ) == 11
        evidence = session.scalars(
            select(Evidence)
            .where(Evidence.source == STYLE_EVIDENCE_SOURCE)
            .order_by(Evidence.sample_id)
        ).all()
        assert len(evidence) == 11
        assert {item.metadata_json["scope_id"] for item in evidence} == {
            "artist-a",
            "artist-b",
        }
        strong = [item for item in evidence if item.metadata_json["strong_outlier"]]
        assert len(strong) == 1
        decisions = session.scalars(
            select(ReviewDecision).where(
                ReviewDecision.category == "style_outlier",
                ReviewDecision.is_active.is_(True),
            )
        ).all()
        assert decisions == []

    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        models_root=tmp_path / "api-models",
    )
    with TestClient(app) as client:
        listed = client.get(f"/api/tasks/{task_id}/reviews/style")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        sample_id = listed.json()["items"][0]["sample_id"]
        folder = listed.json()["items"][0]["artist_scope"]
        scoped = client.get(f"/api/tasks/{task_id}/reviews/style?folder={folder}")
        assert scoped.status_code == 200
        assert scoped.json()["total"] == 1
        empty_scope = client.get(f"/api/tasks/{task_id}/reviews/style?folder=other-folder")
        assert empty_scope.status_code == 200
        assert empty_scope.json()["total"] == 0
        kept = client.post(
            f"/api/tasks/{task_id}/reviews/style/decisions",
            json={"decision": "approved_keep", "sample_ids": [sample_id]},
        )
        assert kept.status_code == 409

    changed = copy.deepcopy(config)
    changed["components"]["cluster.hierarchy"]["config"]["seed"] = 99
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.PAUSED.value
        task.resume_state = TaskStatus.STYLE_ANALYSIS.value
    task_service.update_config(task_id, changed)
    task_service.resume_task(task_id)

    def cache_miss(*_args):
        raise AssertionError("Unchanged style scope identity must use cached results")

    second = StyleAnalyzer(
        task_service,
        runtime_factory=cache_miss,
        project_root=project,
    ).run(
        _claim_style(task_service, "style-cache").token,
        _style_assets(tmp_path),
    )
    assert second.inferred_samples == 0 and second.cached_samples == 11
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((tmp_path / "source").rglob("*.png"))
    ] == list(source_hashes)


def test_style_pause_discards_partial_scope_and_resumes_from_scope_boundary(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id, _ = _prepare_style_task(
        database,
        task_service,
        tmp_path / "source",
        config=_style_config(batch_size=2),
    )
    project = tmp_path / "project"
    project.mkdir()
    pausing_runtime = _FakeStyleRuntime(pause=task_service, task_id=task_id)
    paused = StyleAnalyzer(
        task_service,
        runtime_factory=lambda _config, _assets: pausing_runtime,
        project_root=project,
    ).run(
        _claim_style(task_service, "style-pause").token,
        _style_assets(tmp_path),
    )
    assert paused.processed_samples == 0
    assert paused.final_status == TaskStatus.PAUSED.value
    assert not list(project.rglob("*.part"))
    with database.read_session() as session:
        assert session.scalar(
            select(func.count()).select_from(Evidence).where(
                Evidence.source == STYLE_EVIDENCE_SOURCE
            )
        ) == 0

    task_service.resume_task(task_id)
    resumed_runtime = _FakeStyleRuntime()
    resumed = StyleAnalyzer(
        task_service,
        runtime_factory=lambda _config, _assets: resumed_runtime,
        project_root=project,
    ).run(
        _claim_style(task_service, "style-resume").token,
        _style_assets(tmp_path),
    )
    assert resumed.resumed_from_scope == 0
    assert resumed.inferred_samples == 11
    assert resumed.final_status == TaskStatus.QUEUED.value
    checkpoints = task_service.list_checkpoints(
        task_id, phase=TaskStatus.STYLE_ANALYSIS.value
    )
    assert [checkpoint.batch_index for checkpoint in checkpoints] == [0, 1, 2]
    assert checkpoints[0].cursor["control_only"] is True
    assert not list(project.rglob("*.part"))


def test_style_audit_includes_all_classifications_with_overlay_and_pagination(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id, _ = _prepare_style_task(
        database,
        task_service,
        tmp_path / "source",
        config=_style_config(batch_size=4),
    )
    project = tmp_path / "project"
    project.mkdir()
    StyleAnalyzer(
        task_service,
        runtime_factory=lambda _config, _assets: _FakeStyleRuntime(),
        project_root=project,
    ).run(
        _claim_style(task_service, "style-audit").token,
        _style_assets(tmp_path),
    )

    with database.write_session() as session:
        samples = session.scalars(
            select(Sample)
            .where(Sample.task_id == task_id)
            .order_by(Sample.relative_path, Sample.id)
        ).all()
        evidence = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task_id,
                Evidence.code == "artist_style_score",
                Evidence.source == STYLE_EVIDENCE_SOURCE,
            )
            .order_by(Evidence.sample_id)
        ).all()
        assert len(samples) == len(evidence) == 11
        by_sample = {item.sample_id: item for item in evidence}
        for index, sample in enumerate(samples):
            item = by_sample[sample.id]
            item.value_number = (0.9, 0.4, 0.1)[index] if index < 3 else 0.2
            item.threshold_number = 0.3
            item.severity = ("high", "medium", "info")[index] if index < 3 else "info"
            item.metadata_json = {
                "outlier_reason": ("high distance", "medium distance", None)[index]
                if index < 3
                else None,
            }
        session.add(
            ReviewDecision(
                task_id=task_id,
                sample_id=samples[2].id,
                scope_type="sample",
                scope_id=samples[2].id,
                category="style_outlier",
                decision="approved_keep",
                source="human",
                context_json={"selection": {"sample_ids": [samples[2].id]}},
                is_active=True,
            )
        )

    app = create_app(
        database_path=database.path,
        enforce_runtime=False,
        start_worker=False,
        models_root=tmp_path / "api-models",
    )
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/reviews/style/audit?offset=0&limit=20")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 11
        assert payload["normal"] == 9
        assert payload["outlier"] == 1
        assert payload["strong_outlier"] == 1
        assert payload["pending"] == 2
        assert payload["approved_keep"] == 1
        assert payload["approved_exclude"] == 0
        assert [item["classification"] for item in payload["items"][:3]] == [
            "strong_outlier",
            "outlier",
            "normal",
        ]
        assert payload["items"][2]["decision"] == "approved_keep"
        assert payload["items"][2]["decision_source"] == "human"
        assert payload["items"][3]["decision"] is None
        assert set(payload["items"][0]) == {
            "sample_id",
            "relative_path",
            "artist_scope",
            "style_score",
            "threshold",
            "classification",
            "reason",
            "review_eligible",
            "decision",
            "decision_source",
        }

        folder = "artist-a"
        scoped = client.get(
            f"/api/tasks/{task_id}/reviews/style/audit?offset=1&limit=2&folder={folder}"
        )
        assert scoped.status_code == 200
        assert scoped.json()["total"] == 8
        assert len(scoped.json()["items"]) == 2
        assert all(item["artist_scope"] == folder for item in scoped.json()["items"])
        assert client.get(
            f"/api/tasks/{task_id}/reviews/style/audit?folder=other-folder"
        ).json()["total"] == 0

        legacy = client.get(f"/api/tasks/{task_id}/reviews/style")
        assert legacy.status_code == 200
        assert legacy.json()["total"] == 3
        assert client.post(
            f"/api/tasks/{task_id}/reviews/style/decisions",
            json={"decision": "approved_exclude", "sample_ids": [samples[1].id]},
        ).status_code == 409
