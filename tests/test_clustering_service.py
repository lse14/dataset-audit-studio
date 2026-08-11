from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import dataset_audit_studio.clustering.service as clustering_service_module
import numpy as np
import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.clustering.repository import (
    EMBEDDING_ARTIFACT_KIND,
    SAE_ARTIFACT_KIND,
)
from dataset_audit_studio.clustering.service import SemanticClusterer
from dataset_audit_studio.clustering.types import (
    EmbeddingBatch,
    EmbeddingSample,
)
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import (
    Artifact,
    ClusterMembership,
    ClusterNode,
    Evidence,
    ResolutionAssessment,
    Sample,
    Task,
)
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.scoring.types import (
    AssetFile,
    ModelAsset,
    RuntimeAssets,
)
from PIL import Image
from sqlalchemy import func, select


def _siglip_assets(tmp_path: Path) -> RuntimeAssets:
    return RuntimeAssets(
        models_root=str(tmp_path),
        models=(
            ModelAsset(
                model_id="siglip2_so400m_naflex",
                loader="test_loader",
                root=str(tmp_path / "siglip2_so400m_naflex"),
                files=(
                    AssetFile(
                        path="model.safetensors",
                        size=1,
                        sha256=hashlib.sha256(b"siglip-test").hexdigest(),
                        mtime_ns=1,
                    ),
                ),
                dependencies=(),
                is_custom=False,
                base_model_id=None,
            ),
        ),
    )


def _config(*, sae: bool = True, full_selection: bool = False) -> dict:
    components = materialize_profile("artist_concept")["components"]
    components["media.scan"]["config"]["resolutions"] = [64, 128]
    components["embedding.semantic"]["enabled"] = True
    components["cluster.hierarchy"]["enabled"] = True
    components["embedding.semantic"]["config"].update(
        {
            "device": "cpu",
            "batch_size": 3,
            "shard_size": 64,
        }
    )
    components["cluster.hierarchy"]["config"].update(
        {
            "minimum_split_size": 8,
            "target_leaf_size": 64,
            "kmeans_iterations": 5,
        }
    )
    components["analysis.sae"]["enabled"] = sae
    components["analysis.sae"]["config"].update(
        {
            "feature_count": 8,
            "epochs": 1,
            "batch_size": 8,
            "top_k": 3,
        }
    )
    if full_selection:
        components["selection.three_stage"]["config"][
            "semantic_duplicate_threshold"
        ] = 1.0
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="artist_concept",
        require_profile=True,
    )


def _with_target_leaf_size(config: dict, target_leaf_size: int) -> dict:
    components = copy.deepcopy(config["components"])
    components["cluster.hierarchy"]["config"]["target_leaf_size"] = target_leaf_size
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="artist_concept",
        require_profile=True,
    )


def _with_semantic_duplicate_threshold(config: dict, threshold: float) -> dict:
    components = copy.deepcopy(config["components"])
    components["cluster.hierarchy"]["config"]["semantic_duplicate_threshold"] = threshold
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="artist_concept",
        require_profile=True,
    )


def _prepare_clustering_task(
    database: Database,
    tasks: TaskService,
    source: Path,
    *,
    config: dict,
) -> tuple[str, tuple[str, ...]]:
    source.mkdir()
    task = tasks.create_task(
        name="semantic clustering",
        source_root=str(source),
        output_root=None,
        config=config,
    )
    source_hashes: list[str] = []
    with database.write_session() as session:
        for artist, count in (("artist-a", 6), ("artist-b", 4)):
            for index in range(count):
                path = source / artist / f"image-{index}.png"
                path.parent.mkdir(exist_ok=True)
                Image.new("RGB", (64, 64), (20 * index, 50, 90)).save(path)
                stat = path.stat()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                source_hashes.append(digest)
                sample = Sample(
                    task_id=task.id,
                    relative_path=path.relative_to(source).as_posix(),
                    source_size=stat.st_size,
                    source_mtime_ns=stat.st_mtime_ns,
                    source_sha256=digest,
                    pixel_sha256=digest,
                    media_kind="image",
                    artist_scope=artist,
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
                    phash=hashlib.sha256(f"{artist}:{index}:p".encode()).hexdigest()[:36],
                    colorhash=hashlib.sha256(f"{artist}:{index}:c".encode()).hexdigest()[:14],
                    scan_algorithm_version="test",
                )
                session.add(sample)
                session.flush()
                core_member = not (artist == "artist-a" and index == 5)
                session.add(
                    Evidence(
                        task_id=task.id,
                        sample_id=sample.id,
                        code="artist_style_score",
                        source="artist_style_v1",
                        value_json=100.0 if core_member else 0.0,
                        threshold_json=50.0,
                        value_number=100.0 if core_member else 0.0,
                        threshold_number=50.0,
                        metadata_json={
                            "core_member": core_member,
                            "strong_outlier": not core_member,
                            "scope_id": artist,
                        },
                        severity="info" if core_member else "high",
                        review_only=True,
                        bbox_json=None,
                        algorithm_version="test-style",
                    )
                )
                session.add(
                    Evidence(
                        task_id=task.id,
                        sample_id=sample.id,
                        code="aesthetic_score",
                        source="aesthetic_lse14",
                        value_json=4.5,
                        threshold_json=None,
                        value_number=4.5,
                        threshold_number=None,
                        metadata_json={},
                        severity="info",
                        review_only=False,
                        bbox_json=None,
                        algorithm_version="test-aesthetic",
                    )
                )
                for resolution, eligible in ((64, True), (128, False)):
                    session.add(
                        ResolutionAssessment(
                            task_id=task.id,
                            sample_id=sample.id,
                            resolution=resolution,
                            config_hash="scan-test",
                            area_pixels=4096,
                            minimum_area=resolution * resolution,
                            area_pass=eligible,
                            bucket_width=resolution,
                            bucket_height=resolution,
                            upscale_factor=1.0 if eligible else 2.0,
                            crop_loss=0.0,
                            aspect_ratio=1.0,
                            eligible=eligible,
                            risk_codes=[] if eligible else ["minimum_area"],
                        )
                    )
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.QUEUED.value
        row.resume_state = TaskStatus.SEMANTIC_CLUSTERING.value
    return task.id, tuple(source_hashes)


def _claim_clustering(tasks: TaskService, owner: str):
    claimed = tasks.claim_next(owner=owner, lease_seconds=120)
    assert claimed is not None
    assert claimed.task.status == TaskStatus.SEMANTIC_CLUSTERING.value
    return claimed


class _FakeEmbeddingRuntime:
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

    def embed(self, samples: tuple[EmbeddingSample, ...]) -> EmbeddingBatch:
        self.calls += 1
        rows: list[list[float]] = []
        for sample in samples:
            index = int(Path(sample.relative_path).stem.rsplit("-", 1)[-1])
            if sample.artist_scope == "artist-a":
                rows.append([1.0, index / 100.0, 0.0, 0.0])
            else:
                rows.append([0.0, 0.0, 1.0, index / 100.0])
        if self.pause is not None and self.calls == 1:
            assert self.task_id is not None
            self.pause.request_pause(self.task_id)
        return EmbeddingBatch(
            sample_ids=tuple(sample.sample_id for sample in samples),
            embeddings=np.asarray(rows, dtype=np.float32),
        )

    def close(self) -> None:
        self.closed = True


def test_artist_clustering_persists_separate_scopes_sae_and_reuses_embeddings(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    config = _config()
    task_id, source_hashes = _prepare_clustering_task(
        database,
        task_service,
        tmp_path / "source",
        config=config,
    )
    project = tmp_path / "project"
    project.mkdir()
    runtime = _FakeEmbeddingRuntime()
    first = SemanticClusterer(
        task_service,
        runtime_factory=lambda _config, _assets: runtime,
        project_root=project,
    ).run(
        _claim_clustering(task_service, "cluster-one").token,
        _siglip_assets(tmp_path),
    )
    assert first.eligible_samples == 9
    assert first.embedding_shards == 1
    assert first.inferred_samples == 9 and first.cached_samples == 0
    assert first.cluster_scopes == 2 and first.cluster_nodes == 2
    assert first.sae_features == 8
    assert first.final_status == TaskStatus.EVIDENCE_REVIEW.value
    assert runtime.closed is True
    assert task_service.get_task(task_id).resume_state is None

    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 2
        kinds = set(session.scalars(select(Artifact.kind)).all())
        assert kinds == {EMBEDDING_ARTIFACT_KIND, SAE_ARTIFACT_KIND}
        nodes = session.scalars(select(ClusterNode).order_by(ClusterNode.scope_id)).all()
        assert [(node.scope_id, node.size) for node in nodes] == [
            ("artist-a", 5),
            ("artist-b", 4),
        ]
        memberships = session.execute(
            select(ClusterNode.scope_id, Sample.artist_scope)
            .join(ClusterMembership, ClusterMembership.cluster_id == ClusterNode.id)
            .join(Sample, Sample.id == ClusterMembership.sample_id)
        ).all()
        assert len(memberships) == 9
        assert all(scope_id == artist_scope for scope_id, artist_scope in memberships)

    changed = _with_target_leaf_size(config, 96)
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.PAUSED.value
        task.resume_state = TaskStatus.SEMANTIC_CLUSTERING.value
    task_service.update_config(task_id, changed)
    task_service.resume_task(task_id)

    def cache_miss(*_args):
        raise AssertionError("Hierarchy-only changes must reuse SigLIP embeddings")

    second = SemanticClusterer(
        task_service,
        runtime_factory=cache_miss,
        project_root=project,
    ).run(
        _claim_clustering(task_service, "cluster-cache").token,
        _siglip_assets(tmp_path),
    )
    assert second.inferred_samples == 0 and second.cached_samples == 9
    assert second.sae_features == 8
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((tmp_path / "source").rglob("*.png"))
    ] == list(source_hashes)


def test_compatibility_threshold_change_reuses_embeddings_and_replaces_evidence(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    original = _config(sae=False)
    task_id, _ = _prepare_clustering_task(
        database,
        task_service,
        tmp_path / "source",
        config=original,
    )
    project = tmp_path / "project"
    project.mkdir()
    first_runtime = _FakeEmbeddingRuntime()
    SemanticClusterer(
        task_service,
        runtime_factory=lambda _config, _assets: first_runtime,
        project_root=project,
    ).run(
        _claim_clustering(task_service, "compat-threshold-first").token,
        _siglip_assets(tmp_path),
    )

    with database.read_session() as session:
        first_artifact_sha = session.scalar(
            select(Artifact.sha256).where(
                Artifact.task_id == task_id,
                Artifact.kind == EMBEDDING_ARTIFACT_KIND,
            )
        )
        first_evidence_count = session.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(
                Evidence.task_id == task_id,
                Evidence.code == "duplicate_semantic",
            )
        )
    assert first_artifact_sha
    assert first_evidence_count == 9

    changed = _with_semantic_duplicate_threshold(original, 1.0)
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.PAUSED.value
        task.resume_state = TaskStatus.SEMANTIC_CLUSTERING.value
    task_service.update_config(task_id, changed)
    task_service.resume_task(task_id)

    second_runtime = _FakeEmbeddingRuntime()
    second = SemanticClusterer(
        task_service,
        runtime_factory=lambda _config, _assets: second_runtime,
        project_root=project,
    ).run(
        _claim_clustering(task_service, "compat-threshold-second").token,
        _siglip_assets(tmp_path),
    )

    assert second.cached_samples == 9
    assert second.inferred_samples == 0
    assert second_runtime.calls == 0
    with database.read_session() as session:
        second_artifact_sha = session.scalar(
            select(Artifact.sha256).where(
                Artifact.task_id == task_id,
                Artifact.kind == EMBEDDING_ARTIFACT_KIND,
            )
        )
        second_evidence_count = session.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(
                Evidence.task_id == task_id,
                Evidence.code == "duplicate_semantic",
            )
        )
    assert second_artifact_sha == first_artifact_sha
    assert second_evidence_count == 0


def test_registered_embedding_tampering_is_rejected_even_when_container_is_valid(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    config = _config(sae=False)
    task_id, _ = _prepare_clustering_task(
        database,
        task_service,
        tmp_path / "source",
        config=config,
    )
    project = tmp_path / "project"
    project.mkdir()
    SemanticClusterer(
        task_service,
        runtime_factory=lambda _config, _assets: _FakeEmbeddingRuntime(),
        project_root=project,
    ).run(
        _claim_clustering(task_service, "tamper-one").token,
        _siglip_assets(tmp_path),
    )
    with database.read_session() as session:
        artifact = session.scalar(
            select(Artifact).where(Artifact.kind == EMBEDDING_ARTIFACT_KIND)
        )
        assert artifact is not None
        artifact_path = project.joinpath(*Path(artifact.path).parts)
    with artifact_path.open("r+b") as handle:
        handle.seek(-1, 2)
        original = handle.read(1)
        handle.seek(-1, 2)
        handle.write(bytes([original[0] ^ 1]))

    changed = _with_target_leaf_size(config, 96)
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.PAUSED.value
        task.resume_state = TaskStatus.SEMANTIC_CLUSTERING.value
    task_service.update_config(task_id, changed)
    task_service.resume_task(task_id)
    with pytest.raises(RuntimeError, match="artifact changed on disk"):
        SemanticClusterer(
            task_service,
            runtime_factory=lambda *_args: _FakeEmbeddingRuntime(),
            project_root=project,
        ).run(
            _claim_clustering(task_service, "tamper-two").token,
            _siglip_assets(tmp_path),
        )


def test_embedding_pause_commits_no_partial_artifact_and_resumes_current_shard(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id, _ = _prepare_clustering_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(sae=False),
    )
    project = tmp_path / "project"
    project.mkdir()
    pausing_runtime = _FakeEmbeddingRuntime(pause=task_service, task_id=task_id)
    paused = SemanticClusterer(
        task_service,
        runtime_factory=lambda _config, _assets: pausing_runtime,
        project_root=project,
    ).run(
        _claim_clustering(task_service, "embedding-pause").token,
        _siglip_assets(tmp_path),
    )
    assert paused.inferred_samples == 0
    assert paused.embedding_shards == 0
    assert paused.final_status == TaskStatus.PAUSED.value
    assert not list(project.rglob("*.part"))
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0

    task_service.resume_task(task_id)
    runtime = _FakeEmbeddingRuntime()
    resumed = SemanticClusterer(
        task_service,
        runtime_factory=lambda _config, _assets: runtime,
        project_root=project,
    ).run(
        _claim_clustering(task_service, "embedding-resume").token,
        _siglip_assets(tmp_path),
    )
    assert resumed.inferred_samples == 9
    assert resumed.embedding_shards == 1
    assert resumed.final_status == TaskStatus.EVIDENCE_REVIEW.value
    checkpoints = task_service.list_checkpoints(
        task_id, phase=TaskStatus.SEMANTIC_CLUSTERING.value
    )
    assert [checkpoint.batch_index for checkpoint in checkpoints] == [0, 1, 2, 3]
    assert checkpoints[0].cursor["control_only"] is True
    assert not list(project.rglob("*.part"))


def test_clustering_resume_summary_counts_nodes_from_committed_scopes(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_id, _ = _prepare_clustering_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(sae=False),
    )
    project = tmp_path / "project"
    project.mkdir()
    original = clustering_service_module.hierarchical_clusters
    calls = 0

    def pause_after_first_scope(*args, **kwargs):
        nonlocal calls
        nodes = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            task_service.request_pause(task_id)
        return nodes

    monkeypatch.setattr(
        clustering_service_module,
        "hierarchical_clusters",
        pause_after_first_scope,
    )
    paused = SemanticClusterer(
        task_service,
        runtime_factory=lambda _config, _assets: _FakeEmbeddingRuntime(),
        project_root=project,
    ).run(
        _claim_clustering(task_service, "scope-pause").token,
        _siglip_assets(tmp_path),
    )
    assert paused.cluster_nodes == 1
    assert paused.final_status == TaskStatus.PAUSED.value
    with database.read_session() as session:
        paused_semantic_sample_ids = session.scalars(
            select(Evidence.sample_id).where(
                Evidence.task_id == task_id,
                Evidence.code == "duplicate_semantic",
            )
        ).all()
    assert len(paused_semantic_sample_ids) == 5
    assert len(set(paused_semantic_sample_ids)) == 5

    monkeypatch.setattr(
        clustering_service_module,
        "hierarchical_clusters",
        original,
    )
    task_service.resume_task(task_id)

    def unexpected_inference(*_args):
        raise AssertionError("Committed embedding shards must be reused on resume")

    resumed = SemanticClusterer(
        task_service,
        runtime_factory=unexpected_inference,
        project_root=project,
    ).run(
        _claim_clustering(task_service, "scope-resume").token,
        _siglip_assets(tmp_path),
    )
    assert resumed.cluster_nodes == 2
    assert resumed.final_status == TaskStatus.EVIDENCE_REVIEW.value
    with database.read_session() as session:
        resumed_semantic_sample_ids = session.scalars(
            select(Evidence.sample_id).where(
                Evidence.task_id == task_id,
                Evidence.code == "duplicate_semantic",
            )
        ).all()
    assert len(resumed_semantic_sample_ids) == 9
    assert len(set(resumed_semantic_sample_ids)) == 9
