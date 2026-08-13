from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.database.enums import ArtifactState, TaskStatus
from dataset_audit_studio.database.models import (
    Artifact,
    Evidence,
    ModelResult,
    PhaseCheckpoint,
    ResolutionAssessment,
    ReviewDecision,
    Sample,
)
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.scanner import service as scanner_service
from dataset_audit_studio.scanner.metrics import METRICS_ALGORITHM_VERSION
from dataset_audit_studio.scanner.repository import prepare_scan, upsert_scanned_batch
from dataset_audit_studio.scanner.service import SCAN_ALGORITHM_VERSION, DatasetScanner
from dataset_audit_studio.scanner.types import MetricEvidence, ScannedMedia
from PIL import Image
from sqlalchemy import func, select


def _save(path: Path, color: str, *, alpha: int = 255) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (64, 64), color).putalpha(alpha)
    image = Image.new("RGBA", (64, 64), color)
    image.putalpha(alpha)
    image.save(path)


def _source_snapshot(source: Path) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        data = path.read_bytes()
        stat = path.stat()
        snapshot[path.relative_to(source).as_posix()] = (
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(data).hexdigest(),
        )
    return snapshot


@pytest.fixture
def isolated_scanner_root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def _queued_scan(service: TaskService, source: Path, *, batch_size: int = 2):
    task = service.create_task(
        name="scanner integration",
        source_root=str(source),
        output_root=None,
        config=_scan_config(
            recursive=True,
            resolutions=[64, 1216, 1536],
            batch_size=batch_size,
            cpu_workers=2,
        ),
    )
    return service.queue_task(task.id)


def _scan_config(**updates: object) -> dict:
    components = materialize_profile("general")["components"]
    components["media.scan"]["config"].update(updates)
    return ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )


def test_scanner_persists_media_metrics_and_keeps_source_read_only(
    database: Database,
    task_service: TaskService,
    isolated_scanner_root: Path,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _save(source / "artist" / "valid.png", "#2458c7")
    (source / "artist" / "valid.txt").write_bytes(b"artist, blue clothes\n")
    _save(source / "artist" / "transparent.png", "#ffffff", alpha=0)
    (source / "artist" / "corrupt.webp").write_bytes(b"broken")
    before = _source_snapshot(source)

    queued = _queued_scan(task_service, source)
    claimed = task_service.claim_next(owner="scanner", lease_seconds=120)
    assert claimed is not None
    summary = DatasetScanner(
        task_service,
        project_root=isolated_scanner_root,
    ).run_scanning(claimed.token)

    assert summary.discovered == 3
    assert summary.processed == 3
    assert summary.valid == 1
    assert summary.hard_rejected == 1
    assert summary.decode_errors == 1
    assert not hasattr(summary, "missing_" + "caption")
    assert summary.final_status == TaskStatus.QUEUED.value
    assert _source_snapshot(source) == before

    with database.read_session() as session:
        samples = session.scalars(
            select(Sample).where(Sample.task_id == queued.id).order_by(Sample.relative_path)
        ).all()
        assert [(sample.relative_path, sample.scan_state) for sample in samples] == [
            ("artist/corrupt.webp", "decode_error"),
            ("artist/transparent.png", "hard_reject"),
            ("artist/valid.png", "valid"),
        ]
        valid = next(sample for sample in samples if sample.scan_state == "valid")
        assert valid.artist_scope == "artist"
        assert valid.phash is not None and len(valid.phash) == 36
        assert valid.colorhash is not None
        assert session.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.sample_id == valid.id)
        ) >= 14
        assessments = session.scalars(
            select(ResolutionAssessment)
            .where(ResolutionAssessment.sample_id == valid.id)
            .order_by(ResolutionAssessment.resolution)
        ).all()
        assert [(item.resolution, item.eligible) for item in assessments] == [
            (64, True),
            (1216, False),
            (1536, False),
        ]


def test_scanner_pause_resume_uses_manifest_checkpoint(
    task_service: TaskService,
    isolated_scanner_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _save(source / "a.png", "red")
    _save(source / "b.png", "blue")
    before = _source_snapshot(source)
    queued = _queued_scan(task_service, source, batch_size=1)
    claimed = task_service.claim_next(owner="scanner-one", lease_seconds=120)
    assert claimed is not None
    scanner = DatasetScanner(task_service, project_root=isolated_scanner_root)
    original = scanner._scan_one
    pause_requested = False

    def scan_then_pause(*args, **kwargs):
        nonlocal pause_requested
        result = original(*args, **kwargs)
        if not pause_requested:
            pause_requested = True
            task_service.request_pause(queued.id)
        return result

    monkeypatch.setattr(scanner, "_scan_one", scan_then_pause)
    paused = scanner.run_scanning(claimed.token)
    assert paused.processed == 1
    assert paused.final_status == TaskStatus.PAUSED.value

    task_service.resume_task(queued.id)
    claimed_again = task_service.claim_next(owner="scanner-two", lease_seconds=120)
    assert claimed_again is not None
    monkeypatch.setattr(scanner, "_scan_one", original)
    resumed = scanner.run_scanning(claimed_again.token)
    assert resumed.resumed_from_index == 1
    assert resumed.processed == 2
    assert resumed.final_status == TaskStatus.QUEUED.value
    assert _source_snapshot(source) == before


def test_scanner_groups_complete_decode_batches_into_one_atomic_write(
    database: Database,
    task_service: TaskService,
    isolated_scanner_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for index, color in enumerate(("red", "blue", "green", "purple", "orange")):
        _save(source / f"{index}.png", color)
    queued = _queued_scan(task_service, source, batch_size=2)
    claimed = task_service.claim_next(owner="scanner", lease_seconds=120)
    assert claimed is not None
    commits: list[dict[str, object]] = []
    original_commit = task_service.commit_batch

    def record_commit(*args, **kwargs):
        commits.append(dict(kwargs))
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(task_service, "commit_batch", record_commit)
    summary = DatasetScanner(task_service, project_root=isolated_scanner_root).run_scanning(
        claimed.token
    )

    data_commits = [item for item in commits if item.get("batch_writer") is not None]
    assert summary.processed == 5
    assert len(data_commits) == 1
    assert data_commits[0]["cursor"]["next_index"] == 5
    with database.read_session() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Sample).where(Sample.task_id == queued.id)
            )
            == 5
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PhaseCheckpoint)
                .where(PhaseCheckpoint.task_id == queued.id)
            )
            == 1
        )


def test_scanner_pause_flushes_validated_prefix_and_resumes_from_committed_index(
    task_service: TaskService,
    isolated_scanner_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for index, color in enumerate(("red", "blue", "green", "purple", "orange")):
        _save(source / f"{index}.png", color)
    queued = _queued_scan(task_service, source, batch_size=2)
    claimed = task_service.claim_next(owner="scanner", lease_seconds=120)
    assert claimed is not None
    scanner = DatasetScanner(task_service, project_root=isolated_scanner_root)
    original = scanner._scan_one
    scanned = 0

    def scan_then_pause(*args, **kwargs):
        nonlocal scanned
        result = original(*args, **kwargs)
        scanned += 1
        if scanned == 4:
            task_service.request_pause(queued.id)
        return result

    monkeypatch.setattr(scanner, "_scan_one", scan_then_pause)
    paused = scanner.run_scanning(claimed.token)

    assert paused.processed == 4
    assert paused.final_status == TaskStatus.PAUSED.value
    task_service.resume_task(queued.id)
    resumed_token = task_service.claim_next(owner="scanner-resume", lease_seconds=120)
    assert resumed_token is not None
    monkeypatch.setattr(scanner, "_scan_one", original)
    resumed = scanner.run_scanning(resumed_token.token)
    assert resumed.resumed_from_index == 4
    assert resumed.processed == 5


def test_scanner_write_failure_rolls_back_aggregated_rows_and_checkpoint(
    database: Database,
    task_service: TaskService,
    isolated_scanner_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _save(source / "a.png", "red")
    _save(source / "b.png", "blue")
    queued = _queued_scan(task_service, source, batch_size=2)
    claimed = task_service.claim_next(owner="scanner", lease_seconds=120)
    assert claimed is not None

    def fail_upsert(*_args, **_kwargs):
        raise RuntimeError("injected scan write failure")

    monkeypatch.setattr(scanner_service, "upsert_scanned_batch", fail_upsert)
    with pytest.raises(RuntimeError, match="injected scan write failure"):
        DatasetScanner(task_service, project_root=isolated_scanner_root).run_scanning(claimed.token)

    with database.read_session() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Sample).where(Sample.task_id == queued.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PhaseCheckpoint)
                .where(PhaseCheckpoint.task_id == queued.id)
            )
            == 0
        )
    assert task_service.get_task(queued.id).progress_current == 0


def test_manifest_sha_tampering_blocks_resume(
    task_service: TaskService,
    isolated_scanner_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _save(source / "a.png", "red")
    _save(source / "b.png", "blue")
    queued = _queued_scan(task_service, source, batch_size=1)
    claimed = task_service.claim_next(owner="scanner-one", lease_seconds=120)
    assert claimed is not None
    scanner = DatasetScanner(task_service, project_root=isolated_scanner_root)
    original = scanner._scan_one
    requested = False

    def pause_once(*args, **kwargs):
        nonlocal requested
        result = original(*args, **kwargs)
        if not requested:
            requested = True
            task_service.request_pause(queued.id)
        return result

    monkeypatch.setattr(scanner, "_scan_one", pause_once)
    scanner.run_scanning(claimed.token)
    checkpoint = task_service.list_checkpoints(queued.id)[-1]
    manifest = isolated_scanner_root.joinpath(*Path(checkpoint.cursor["manifest_path"]).parts)
    with manifest.open("ab") as stream:
        stream.write(b"tampered\n")

    task_service.resume_task(queued.id)
    claimed_again = task_service.claim_next(owner="scanner-two", lease_seconds=120)
    assert claimed_again is not None
    monkeypatch.setattr(scanner, "_scan_one", original)
    with pytest.raises(ValueError, match="SHA-256"):
        scanner.run_scanning(claimed_again.token)
    task_service.request_terminate(queued.id, force=True)


def test_forced_termination_discards_uncommitted_scan_batch(
    database: Database,
    task_service: TaskService,
    isolated_scanner_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _save(source / "a.png", "red")
    queued = _queued_scan(task_service, source, batch_size=1)
    claimed = task_service.claim_next(owner="scanner", lease_seconds=120)
    assert claimed is not None
    scanner = DatasetScanner(task_service, project_root=isolated_scanner_root)
    original = scanner._scan_one

    def scan_then_terminate(*args, **kwargs):
        result = original(*args, **kwargs)
        task_service.request_terminate(queued.id, force=True)
        return result

    monkeypatch.setattr(scanner, "_scan_one", scan_then_terminate)
    summary = scanner.run_scanning(claimed.token)
    assert summary.final_status == TaskStatus.TERMINATED.value
    with database.read_session() as session:
        assert session.scalar(
            select(func.count()).select_from(Sample).where(Sample.task_id == queued.id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(PhaseCheckpoint)
            .where(PhaseCheckpoint.task_id == queued.id)
        ) == 0


def _scanned(
    relative_path: str,
    source_sha: str,
    pixel_sha: str,
) -> ScannedMedia:
    return ScannedMedia(
        relative_path=relative_path,
        source_size=100,
        source_mtime_ns=1,
        source_sha256=source_sha,
        pixel_sha256=pixel_sha,
        media_kind="image",
        artist_scope="__root__",
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
        phash="0" * 36,
        colorhash="0" * 14,
        evidence=(),
        resolutions=(),
    )


def _upsert(database: Database, task_id: str, item: ScannedMedia, *, prepare: bool) -> None:
    with database.write_session() as session:
        if prepare:
            prepare_scan(session, task_id)
        upsert_scanned_batch(
            session,
            task_id=task_id,
            config_hash="c" * 64,
            active_paths={item.relative_path},
            items=(item,),
            algorithm_version=SCAN_ALGORITHM_VERSION,
        )


def test_incremental_reuse_move_and_layered_invalidation(
    database: Database, task_service: TaskService
) -> None:
    task = task_service.create_task(
        name="incremental",
        source_root="E:\\source",
        output_root=None,
        config=_scan_config(),
    )
    original = _scanned("old.png", "1" * 64, "a" * 64)
    _upsert(database, task.id, original, prepare=True)

    with database.write_session() as session:
        sample = session.scalar(select(Sample).where(Sample.task_id == task.id))
        assert sample is not None
        sample_id = sample.id
        session.add_all(
            [
                ModelResult(
                    task_id=task.id,
                    sample_id=sample_id,
                    model_id="model",
                    model_sha256="2" * 64,
                    preprocessing_version="v1",
                    config_hash="c" * 64,
                    result_json={"score": 1},
                ),
                Evidence(
                    task_id=task.id,
                    sample_id=sample_id,
                    code="aesthetic",
                    source="aesthetic_model",
                    value_json=1.0,
                    value_number=1.0,
                    threshold_json=None,
                    threshold_number=None,
                    metadata_json={},
                    severity="info",
                    review_only=False,
                    bbox_json=None,
                    algorithm_version="v1",
                ),
                ReviewDecision(
                    task_id=task.id,
                    sample_id=sample_id,
                    scope_type="sample",
                    scope_id=sample_id,
                    category="risk",
                    decision="keep",
                    source="human",
                    context_json={},
                    is_active=True,
                ),
                Artifact(
                    task_id=task.id,
                    sample_id=sample_id,
                    kind="thumbnail",
                    phase="scanning",
                    cache_key="thumb",
                    path="data/thumb.png",
                    state=ArtifactState.READY.value,
                    metadata_json={},
                ),
            ]
        )

    moved = _scanned("new.png", original.source_sha256, original.pixel_sha256 or "")
    _upsert(database, task.id, moved, prepare=True)
    with database.read_session() as session:
        sample = session.scalar(select(Sample).where(Sample.task_id == task.id))
        assert sample is not None
        assert sample.id == sample_id
        assert sample.relative_path == "new.png"
        assert session.scalar(select(func.count()).select_from(ModelResult)) == 1

    reencoded = _scanned("new.png", "3" * 64, moved.pixel_sha256 or "")
    _upsert(database, task.id, reencoded, prepare=False)
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(ModelResult)) == 1

    changed_pixels = _scanned("new.png", "4" * 64, "b" * 64)
    _upsert(database, task.id, changed_pixels, prepare=False)
    with database.read_session() as session:
        assert session.scalar(select(func.count()).select_from(ModelResult)) == 0
        assert session.scalar(select(func.count()).select_from(Evidence)) == 0
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0
        review = session.scalar(select(ReviewDecision))
        assert review is not None and review.is_active is False


def test_rescan_replaces_all_historical_technical_metric_evidence(
    database: Database, task_service: TaskService
) -> None:
    task = task_service.create_task(
        name="technical metric cleanup",
        source_root="E:\\source",
        output_root=None,
        config=_scan_config(),
    )
    original = _scanned("sample.png", "1" * 64, "a" * 64)
    _upsert(database, task.id, original, prepare=True)

    with database.write_session() as session:
        sample = session.scalar(select(Sample).where(Sample.task_id == task.id))
        assert sample is not None
        for source in ("scanner", "technical_metrics_v0", "technical_metrics_v1"):
            session.add(
                Evidence(
                    task_id=task.id,
                    sample_id=sample.id,
                    code=source,
                    source=source,
                    value_json=1.0,
                    value_number=1.0,
                    threshold_json=None,
                    threshold_number=None,
                    metadata_json={},
                    severity="info",
                    review_only=False,
                    bbox_json=None,
                    algorithm_version=source,
                )
            )
        session.add(
            Evidence(
                task_id=task.id,
                sample_id=sample.id,
                code="aesthetic",
                source="aesthetic_model",
                value_json=1.0,
                value_number=1.0,
                threshold_json=None,
                threshold_number=None,
                metadata_json={},
                severity="info",
                review_only=False,
                bbox_json=None,
                algorithm_version="aesthetic_v1",
            )
        )

    v2_metric = MetricEvidence(
        code="rgb_entropy",
        value=7.0,
        threshold=2.5,
        severity="info",
        review_only=False,
        source=METRICS_ALGORITHM_VERSION,
        metadata={"algorithm_version": METRICS_ALGORITHM_VERSION},
    )
    _upsert(database, task.id, replace(original, evidence=(v2_metric,)), prepare=False)

    with database.read_session() as session:
        rows = session.scalars(
            select(Evidence)
            .where(Evidence.task_id == task.id)
            .order_by(Evidence.source, Evidence.code)
        ).all()

    assert [(row.source, row.code) for row in rows] == [
        ("aesthetic_model", "aesthetic"),
        (METRICS_ALGORITHM_VERSION, "rgb_entropy"),
    ]


def test_cpu_metric_finalizer_honors_pause_race(
    task_service: TaskService, monkeypatch
) -> None:
    task = task_service.create_task(
        name="cpu race",
        source_root="E:\\source",
        output_root=None,
        config=_scan_config(),
    )
    task_service.queue_task(task.id)
    scan_claim = task_service.claim_next(owner="scan", lease_seconds=120)
    assert scan_claim is not None
    task_service.complete_phase(scan_claim.token, phase=TaskStatus.SCANNING)
    cpu_claim = task_service.claim_next(owner="cpu", lease_seconds=120)
    assert cpu_claim is not None

    original_complete = task_service.complete_phase

    def pause_before_complete(token, *, phase):
        task_service.request_pause(task.id)
        return original_complete(token, phase=phase)

    monkeypatch.setattr(task_service, "complete_phase", pause_before_complete)
    status = DatasetScanner(task_service).finalize_precomputed_cpu_metrics(cpu_claim.token)
    assert status == TaskStatus.PAUSED.value
    paused = task_service.get_task(task.id)
    assert paused.resume_state == TaskStatus.CPU_METRICS.value
