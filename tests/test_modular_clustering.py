from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

import dataset_audit_studio.app.modular_clustering as modular_clustering
import numpy as np
import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.modular_clustering import (
    ModularClusteringComponentService,
    finalize_modular_clustering,
)
from dataset_audit_studio.app.modular_clustering_coordinator import (
    ModularClusteringCoordinator,
    build_clustering_component_plan,
)
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.clustering.repository import (
    EMBEDDING_ARTIFACT_KIND,
    ClusteringRepository,
)
from dataset_audit_studio.components.cluster_hierarchy.config import HierarchyConfig
from dataset_audit_studio.components.cluster_hierarchy.contracts import ClusterPlanNode
from dataset_audit_studio.components.semantic_embedding.contracts import (
    SemanticEmbeddingBatch,
)
from dataset_audit_studio.core.model_assets import AssetFile, ModelAsset, RuntimeAssets
from dataset_audit_studio.database.enums import ReviewState, TaskStatus
from dataset_audit_studio.database.models import (
    Artifact,
    ClusterNode,
    Evidence,
    ReviewDecision,
    Sample,
    Task,
    TaskConfig,
)
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.export_runs.eligibility import EligibilityResolver
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.reviews.service import ReviewService
from dataset_audit_studio.reviews.types import CuratedReviewSelection
from dataset_audit_studio.scoring.repository import ScoringRepository
from dataset_audit_studio.scoring.types import SampleInput
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import func, select

ORDER = ("embedding.semantic", "analysis.sae", "cluster.hierarchy")


def _assets(tmp_path: Path) -> RuntimeAssets:
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


def _config(*, sae: bool = True, seed: int = 7) -> dict:
    components = materialize_profile("general")["components"]
    components["embedding.semantic"]["enabled"] = True
    components["cluster.hierarchy"]["enabled"] = True
    components["embedding.semantic"]["config"].update(
        {
            "device": "cpu",
            "batch_size": 4,
            "shard_size": 64,
        }
    )
    components["cluster.hierarchy"]["config"].update(
        {
            "minimum_split_size": 8,
            "target_leaf_size": 16,
            "kmeans_iterations": 5,
            "seed": seed,
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
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def _with_seed(config: dict, seed: int) -> dict:
    components = copy.deepcopy(config["components"])
    components["cluster.hierarchy"]["config"]["seed"] = seed
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def test_hierarchy_materializes_semantic_duplicate_threshold() -> None:
    components = materialize_profile("general")["components"]
    components["embedding.semantic"]["enabled"] = True
    components["cluster.hierarchy"]["enabled"] = True
    components["cluster.hierarchy"]["config"]["semantic_duplicate_threshold"] = 0.992

    materialized = ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )

    assert materialized["components"]["cluster.hierarchy"]["config"][
        "semantic_duplicate_threshold"
    ] == 0.992
    assert materialized["clustering"]["semantic_duplicate_threshold"] == 0.992

    with pytest.raises(ValidationError):
        HierarchyConfig(semantic_duplicate_threshold=0.799)
    with pytest.raises(ValidationError):
        HierarchyConfig(semantic_duplicate_threshold=1.001)


def _prepare_task(
    database: Database,
    tasks: TaskService,
    source: Path,
    *,
    config: dict,
    count: int,
    scopes: tuple[str, ...] | None = None,
) -> str:
    if scopes is not None and len(scopes) != count:
        raise ValueError("Scope count must match sample count")
    source.mkdir()
    task = tasks.create_task(
        name="modular clustering",
        source_root=str(source),
        output_root=None,
        config=config,
    )
    with database.write_session() as session:
        for index in range(count):
            scope = scopes[index] if scopes is not None else "artist"
            path = source / scope / f"image-{index}.png"
            path.parent.mkdir(exist_ok=True)
            Image.new("RGB", (32, 32), (index * 10, 30, 60)).save(path)
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            session.add(
                Sample(
                    task_id=task.id,
                    relative_path=path.relative_to(source).as_posix(),
                    source_size=stat.st_size,
                    source_mtime_ns=stat.st_mtime_ns,
                    source_sha256=digest,
                    pixel_sha256=digest,
                    media_kind="image",
                    artist_scope=scope,
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
                    phash=f"{index:036x}",
                    colorhash=f"{index:014x}",
                    scan_algorithm_version="test",
                )
            )
        row = session.get(Task, task.id)
        assert row is not None
        row.status = TaskStatus.QUEUED.value
        row.resume_state = TaskStatus.SEMANTIC_CLUSTERING.value
    return task.id


def _claim(tasks: TaskService, owner: str):
    claimed = tasks.claim_next(owner=owner, lease_seconds=120)
    assert claimed is not None
    assert claimed.task.status == TaskStatus.SEMANTIC_CLUSTERING.value
    return claimed.token


class _EmbeddingRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def embed(self, samples) -> SemanticEmbeddingBatch:
        self.calls += 1
        matrix = np.zeros((len(samples), 4), dtype=np.float32)
        for row in range(len(samples)):
            matrix[row, row % 4] = 1.0
        return SemanticEmbeddingBatch(
            tuple(sample.sample_id for sample in samples),
            matrix,
        )

    def close(self) -> None:
        self.closed = True


class _CharacterEmbeddingRuntime(_EmbeddingRuntime):
    def embed(self, samples) -> SemanticEmbeddingBatch:
        self.calls += 1
        rows = []
        for sample in samples:
            if sample.artist_scope == "alpha":
                outlier = sample.relative_path.endswith("image-5.png")
                rows.append([0.0, 1.0] if outlier else [1.0, 0.0])
            elif sample.artist_scope == "beta":
                outlier = sample.relative_path.endswith("image-11.png")
                rows.append([1.0, 0.0] if outlier else [0.0, 1.0])
            else:
                rows.append([1.0, 0.0])
        matrix = np.asarray(rows, dtype=np.float32)
        return SemanticEmbeddingBatch(
            tuple(sample.sample_id for sample in samples),
            matrix,
        )


class _LeafDuplicateEmbeddingRuntime(_EmbeddingRuntime):
    def embed(self, samples) -> SemanticEmbeddingBatch:
        self.calls += 1
        matrix = np.asarray(
            [
                [1.0, 0.0, 0.0],
                [0.9999, 0.01, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.9999, 0.01],
            ],
            dtype=np.float32,
        )
        return SemanticEmbeddingBatch(
            tuple(sample.sample_id for sample in samples),
            matrix,
        )


def test_clustering_plan_is_component_scoped_and_sae_is_optional() -> None:
    enabled = build_clustering_component_plan(_config(sae=True))
    assert [(item.component_id, item.model_ids) for item in enabled] == [
        ("embedding.semantic", ("siglip2_so400m_naflex",)),
        ("analysis.sae", ()),
        ("cluster.hierarchy", ()),
    ]
    disabled = build_clustering_component_plan(_config(sae=False))
    assert [item.component_id for item in disabled] == [
        "embedding.semantic",
        "cluster.hierarchy",
    ]


def test_hierarchy_persists_leaf_scoped_semantic_duplicate_evidence(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(sae=False),
        count=4,
    )
    root_key = "test:root"
    nodes = (
        ClusterPlanNode(
            cluster_key=root_key,
            parent_key=None,
            scope_kind="artist",
            scope_id="artist",
            level=0,
            sample_indices=(0, 1, 2, 3),
            centroid=np.asarray([0.5, 0.5, 0.0], dtype=np.float32),
            representative_index=0,
            is_leaf=False,
        ),
        ClusterPlanNode(
            cluster_key="test:leaf-a",
            parent_key=root_key,
            scope_kind="artist",
            scope_id="artist",
            level=1,
            sample_indices=(0, 1),
            centroid=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            representative_index=0,
            is_leaf=True,
        ),
        ClusterPlanNode(
            cluster_key="test:leaf-b",
            parent_key=root_key,
            scope_kind="artist",
            scope_id="artist",
            level=1,
            sample_indices=(2, 3),
            centroid=np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
            representative_index=0,
            is_leaf=True,
        ),
    )
    monkeypatch.setattr(
        modular_clustering,
        "hierarchical_clusters",
        lambda *_args, **_kwargs: nodes,
    )
    runtime = _LeafDuplicateEmbeddingRuntime()
    service = ModularClusteringComponentService(
        task_service,
        embedding_runtime_factory=lambda _config, _assets: runtime,
        project_root=tmp_path,
    )
    order = ("embedding.semantic", "cluster.hierarchy")
    token = _claim(task_service, "semantic-evidence")
    service.run(
        token,
        _assets(tmp_path),
        component_id="embedding.semantic",
        component_order=order,
    )
    service.run(
        token,
        RuntimeAssets(models_root=str(tmp_path), models=()),
        component_id="cluster.hierarchy",
        component_order=order,
    )
    finalize_modular_clustering(task_service, token, component_order=order)

    with database.read_session() as session:
        evidence = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task_id,
                Evidence.code == "duplicate_semantic",
            )
            .order_by(Evidence.sample_id)
        ).all()
        decisions = session.scalars(
            select(ReviewDecision).where(ReviewDecision.task_id == task_id)
        ).all()

    assert len(evidence) == 4
    assert len({row.metadata_json["group_key"] for row in evidence}) == 2
    assert {row.metadata_json["leaf_cluster_key"] for row in evidence} == {
        "test:leaf-a",
        "test:leaf-b",
    }
    assert all(row.source == "semantic_duplicate_siglip2_v1" for row in evidence)
    assert all(row.review_only is True for row in evidence)
    assert all(row.threshold_number == pytest.approx(0.985) for row in evidence)
    assert all(0.985 <= float(row.value_number or 0.0) <= 1.0 for row in evidence)
    assert all(
        {
            "model_id",
            "model_sha256",
            "preprocessing_version",
            "embedding_identity_hash",
            "hierarchy_config_hash",
            "scope_kind",
            "scope_id",
            "provenance",
        }.issubset(row.metadata_json)
        for row in evidence
    )
    assert decisions == []


def test_character_profile_hierarchy_generates_role_review_candidates(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    components = materialize_profile("character_concept")["components"]
    components["embedding.semantic"]["config"].update(
        {"device": "cpu", "batch_size": 4, "shard_size": 64}
    )
    config = ComponentTaskConfigMaterializer().materialize(
        components,
        profile="character_concept",
        require_profile=True,
    )
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "character-source",
        config=config,
        count=15,
        scopes=("alpha",) * 6 + ("beta",) * 6 + ("tiny",) * 3,
    )
    runtime = _CharacterEmbeddingRuntime()
    service = ModularClusteringComponentService(
        task_service,
        embedding_runtime_factory=lambda _config, _assets: runtime,
        project_root=tmp_path,
    )
    order = ("embedding.semantic", "cluster.hierarchy")
    token = _claim(task_service, "character")

    service.run(
        token,
        _assets(tmp_path),
        component_id="embedding.semantic",
        component_order=order,
    )
    service.run(
        token,
        RuntimeAssets(models_root=str(tmp_path), models=()),
        component_id="cluster.hierarchy",
        component_order=order,
    )
    finalize_modular_clustering(task_service, token, component_order=order)

    with database.read_session() as session:
        candidates = session.execute(
            select(Evidence, Sample)
            .join(Sample, Sample.id == Evidence.sample_id)
            .where(
                Evidence.task_id == task_id,
                Evidence.code == "character_role_outlier",
            )
            .order_by(Sample.relative_path)
        ).all()
    assert [row.Sample.relative_path for row in candidates] == [
        "alpha/image-5.png",
        "beta/image-11.png",
    ]
    evidence, sample = candidates[0]
    assert evidence.metadata_json["scope_id"] == "alpha"
    assert evidence.metadata_json["scope_size"] == 6
    assert evidence.metadata_json["model_sha256"]
    assert evidence.metadata_json["preprocessing_version"] == (
        "siglip2-naflex-image-processor-v1"
    )
    assert len(evidence.metadata_json["embedding_identity_hash"]) == 64
    assert evidence.metadata_json["algorithm_config"] == {
        "minimum_scope_size": 4,
        "outlier_sigma": 2.04,
        "max_iterations": 2,
    }
    assert len(evidence.metadata_json["algorithm_config_hash"]) == 64
    checkpoints = task_service.list_checkpoints(
        task_id,
        phase=TaskStatus.SEMANTIC_CLUSTERING.value,
    )
    hierarchy_checkpoint = next(
        item for item in checkpoints if item.cursor["component_id"] == "cluster.hierarchy"
    )
    assert hierarchy_checkpoint.cursor["character_consistency_config_hash"] == (
        evidence.metadata_json["algorithm_config_hash"]
    )

    reviews = ReviewService(database)
    pending = reviews.list_curated_candidates(
        task_id,
        evidence_type="risk",
        reason_code="character_role_outlier",
    )
    assert {
        (item.sample_id, item.reason_code, item.decision) for item in pending.items
    } == {
        (row.Sample.id, row.Evidence.code, "pending_review") for row in candidates
    }
    alpha_pending = reviews.list_curated_candidates(
        task_id,
        evidence_type="risk",
        folder="alpha",
        reason_code="character_role_outlier",
    )
    assert [item.sample_id for item in alpha_pending.items] == [sample.id]
    reviews.decide_curated_candidates(
        task_id,
        selection=CuratedReviewSelection(
            evidence_type="risk",
            sample_ids=(sample.id,),
        ),
        decision=ReviewState.APPROVED_EXCLUDE,
    )

    def eligibility_reason() -> str | None:
        with database.read_session() as session:
            task_row = session.get(Task, task_id)
            assert task_row is not None
            config_row = session.scalar(
                select(TaskConfig).where(
                    TaskConfig.task_id == task_id,
                    TaskConfig.revision == task_row.current_config_revision,
                )
            )
            assert config_row is not None
            rows = list(
                session.scalars(
                    select(Sample).where(Sample.task_id == task_id).order_by(Sample.id)
                ).all()
            )
            result = EligibilityResolver().resolve(
                session,
                task=task_row,
                config=config_row,
                rows=rows,
                settings={
                    "minimum_resolution": 1,
                    "domain_minimum": None,
                    "aesthetic_minimum": None,
                    "style_outlier_mode": "off",
                    "exclude_exact_visual_duplicates": False,
                },
            )
            return result.outcomes[sample.id].reason

    assert eligibility_reason() == "manual_exclude"
    reviews.decide_curated_candidates(
        task_id,
        selection=CuratedReviewSelection(
            evidence_type="risk",
            sample_ids=(sample.id,),
        ),
        decision=ReviewState.APPROVED_KEEP,
    )
    assert eligibility_reason() is None


def test_list_samples_does_not_build_a_sqlite_variable_sized_in_clause(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = task_service.create_task(
        name="large clustering input",
        source_root=str(tmp_path),
        output_root=None,
        config=_config(sae=False),
    )
    inputs = tuple(
        SampleInput(
            sample_id=f"sample-{index}",
            relative_path=f"image-{index}.png",
            artist_scope="artist",
            source_path=tmp_path / f"image-{index}.png",
            image_path=tmp_path / f"image-{index}.png",
            source_size=1,
            source_mtime_ns=1,
            pixel_sha256="a" * 64,
        )
        for index in range(1_001)
    )
    monkeypatch.setattr(
        ScoringRepository,
        "list_inputs",
        lambda _self, _session, _task: inputs,
    )
    with database.write_session() as session:
        session.add_all(
            Sample(
                id=item.sample_id,
                task_id=task.id,
                relative_path=item.relative_path,
                source_size=1,
                source_mtime_ns=1,
                source_sha256="b" * 64,
                pixel_sha256=item.pixel_sha256,
                media_kind="image",
                artist_scope=item.artist_scope,
                scan_state="valid",
            )
            for item in inputs
        )

    with database.read_session() as session:
        samples = ClusteringRepository(project_root=tmp_path).list_samples(
            session,
            task_service.get_task(task.id),
            artist_core_only=False,
        )

    assert len(samples) == len(inputs)


def test_coordinator_spawns_embedding_sae_and_hierarchy_separately(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=_config(),
        count=0,
    )
    assets = _assets(tmp_path)
    requested = []

    def asset_waiter(_token, item):
        requested.append((item.component_id, item.model_ids))
        return assets

    summary = ModularClusteringCoordinator(
        database,
        task_service,
        model_service=None,
        component_asset_waiter=asset_waiter,
        project_root=tmp_path,
        poll_seconds=0.01,
    ).run(_claim(task_service, "spawn"))
    assert summary.final_status == TaskStatus.EVIDENCE_REVIEW.value
    assert task_service.get_task(task_id).resume_state is None
    assert requested == [
        ("embedding.semantic", ("siglip2_so400m_naflex",)),
        ("analysis.sae", ()),
        ("cluster.hierarchy", ()),
    ]
    assert [item["runtime_model_ids"] for item in summary.component_summaries] == [
        ["siglip2_so400m_naflex"],
        [],
        [],
    ]
    assert all(item["process_pid"] != os.getpid() for item in summary.component_summaries)
    checkpoints = task_service.list_checkpoints(
        task_id, phase=TaskStatus.SEMANTIC_CLUSTERING.value
    )
    assert [item.cursor["component_id"] for item in checkpoints] == list(ORDER)
    assert checkpoints[-1].cursor["clusters_prepared"] is True


def test_hierarchy_change_reuses_embedding_artifact_without_runtime(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    original = _config(sae=False)
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=original,
        count=10,
    )
    runtime = _EmbeddingRuntime()
    service = ModularClusteringComponentService(
        task_service,
        embedding_runtime_factory=lambda _config, _assets: runtime,
        project_root=tmp_path,
    )
    order = ("embedding.semantic", "cluster.hierarchy")
    token = _claim(task_service, "first")
    for component in order:
        service.run(
            token,
            _assets(tmp_path) if component == "embedding.semantic" else RuntimeAssets(
                models_root=str(tmp_path), models=()
            ),
            component_id=component,
            component_order=order,
        )
    finalize_modular_clustering(task_service, token, component_order=order)
    assert runtime.calls == 3
    assert runtime.closed is True
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert session.scalar(select(func.count()).select_from(ClusterNode)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.code == "character_role_outlier")
            )
            == 0
        )

    changed = _with_seed(original, 11)
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.PAUSED.value
        task.resume_state = TaskStatus.SEMANTIC_CLUSTERING.value
    task_service.update_config(task_id, changed)
    task_service.resume_task(task_id)
    cached_service = ModularClusteringComponentService(
        task_service,
        embedding_runtime_factory=lambda *_args: (_ for _ in ()).throw(
            AssertionError("unchanged embedding artifact must be reused")
        ),
        project_root=tmp_path,
    )
    token = _claim(task_service, "changed")
    embedding = cached_service.run(
        token,
        _assets(tmp_path),
        component_id="embedding.semantic",
        component_order=order,
    )
    hierarchy = cached_service.run(
        token,
        RuntimeAssets(models_root=str(tmp_path), models=()),
        component_id="cluster.hierarchy",
        component_order=order,
    )
    assert embedding.cached_samples == 10 and embedding.inferred_samples == 0
    assert hierarchy.output_count == 1


def test_semantic_threshold_change_reuses_embeddings_and_replaces_evidence(
    database: Database,
    task_service: TaskService,
    tmp_path: Path,
) -> None:
    original = _config(sae=False)
    task_id = _prepare_task(
        database,
        task_service,
        tmp_path / "source",
        config=original,
        count=4,
    )
    first_runtime = _LeafDuplicateEmbeddingRuntime()
    service = ModularClusteringComponentService(
        task_service,
        embedding_runtime_factory=lambda _config, _assets: first_runtime,
        project_root=tmp_path,
    )
    order = ("embedding.semantic", "cluster.hierarchy")
    token = _claim(task_service, "threshold-first")
    service.run(
        token,
        _assets(tmp_path),
        component_id="embedding.semantic",
        component_order=order,
    )
    service.run(
        token,
        RuntimeAssets(models_root=str(tmp_path), models=()),
        component_id="cluster.hierarchy",
        component_order=order,
    )
    finalize_modular_clustering(task_service, token, component_order=order)
    assert first_runtime.calls == 1

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
    assert first_evidence_count == 4

    changed_components = copy.deepcopy(original["components"])
    changed_components["cluster.hierarchy"]["config"][
        "semantic_duplicate_threshold"
    ] = 1.0
    changed = ComponentTaskConfigMaterializer().materialize(
        changed_components,
        profile="general",
        require_profile=True,
    )
    with database.write_session() as session:
        task = session.get(Task, task_id)
        assert task is not None
        task.status = TaskStatus.PAUSED.value
        task.resume_state = TaskStatus.SEMANTIC_CLUSTERING.value
    task_service.update_config(task_id, changed)
    task_service.resume_task(task_id)

    second_runtime = _LeafDuplicateEmbeddingRuntime()
    cached_service = ModularClusteringComponentService(
        task_service,
        embedding_runtime_factory=lambda _config, _assets: second_runtime,
        project_root=tmp_path,
    )
    token = _claim(task_service, "threshold-second")
    embedding = cached_service.run(
        token,
        _assets(tmp_path),
        component_id="embedding.semantic",
        component_order=order,
    )
    cached_service.run(
        token,
        RuntimeAssets(models_root=str(tmp_path), models=()),
        component_id="cluster.hierarchy",
        component_order=order,
    )

    assert embedding.cached_samples == 4
    assert embedding.inferred_samples == 0
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
