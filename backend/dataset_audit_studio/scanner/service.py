from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select

from dataset_audit_studio.core.profile_contracts import DatasetProfile
from dataset_audit_studio.database.enums import TaskStatus
from dataset_audit_studio.database.models import Evidence, Sample
from dataset_audit_studio.jobs.errors import InvalidTaskTransition, StaleWorkerToken
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.jobs.types import WorkerToken
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.discovery import (
    validate_builtin_profile_input_layout,
)
from dataset_audit_studio.scanner.manifest import build_manifest, load_manifest
from dataset_audit_studio.scanner.media import MediaDecodeError, decode_media
from dataset_audit_studio.scanner.metrics import (
    METRICS_ALGORITHM_VERSION,
    calculate_metrics,
    is_fully_transparent,
    perceptual_hashes,
    pixel_sha256,
)
from dataset_audit_studio.scanner.repository import (
    UNREADABLE_SHA256,
    prepare_scan,
    upsert_manifest_artifact,
    upsert_scanned_batch,
)
from dataset_audit_studio.scanner.resolution import assess_resolutions
from dataset_audit_studio.scanner.types import (
    DiscoveredMedia,
    DiscoveryResult,
    ManifestInfo,
    MetricEvidence,
    ScannedMedia,
    ScanSummary,
)

SCAN_ALGORITHM_VERSION = "dataset_scanner_v1"
COUNTER_KEYS = (
    "processed",
    "valid",
    "hard_rejected",
    "decode_errors",
    "source_changed",
)


def _builtin_profile(task_config: dict) -> DatasetProfile | None:
    value = task_config.get("profile")
    if value is None:
        return None
    try:
        return DatasetProfile(value)
    except (TypeError, ValueError):
        return None


def _scanner_evidence(
    code: str,
    value: str | int | float | bool | None,
    *,
    severity: str,
    review_only: bool = False,
    metadata: dict[str, object] | None = None,
) -> MetricEvidence:
    details = dict(metadata or {})
    details["algorithm_version"] = SCAN_ALGORITHM_VERSION
    return MetricEvidence(
        code=code,
        value=value,
        threshold=None,
        severity=severity,
        review_only=review_only,
        source="scanner",
        metadata=details,
    )


def _manifest_from_cursor(relative_path: str, *, project_root: Path) -> Path:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("Unsafe manifest path in checkpoint")
    project_root = project_root.resolve(strict=False)
    path = project_root.joinpath(*pure.parts).resolve(strict=True)
    path.relative_to(project_root)
    return path


class DatasetScanner:
    def __init__(self, tasks: TaskService, *, project_root: Path | None = None) -> None:
        self.tasks = tasks
        self.project_root = (project_root or PROJECT_ROOT).resolve(strict=False)

    def run_scanning(self, token: WorkerToken) -> ScanSummary:
        task = self.tasks.get_task(token.task_id)
        if task.status != TaskStatus.SCANNING.value:
            raise InvalidTaskTransition(f"Scanner requires scanning status, got {task.status}")
        config = ScanConfig.from_task_config(task.config)
        source_root = Path(task.source_root).resolve(strict=True)
        checkpoints = [
            checkpoint
            for checkpoint in self.tasks.list_checkpoints(task.id, phase=TaskStatus.SCANNING.value)
            if checkpoint.config_hash == task.config_hash
        ]
        checkpoints.sort(key=lambda item: item.batch_index)
        latest = checkpoints[-1] if checkpoints else None

        if latest is None:
            if _builtin_profile(task.config) is not None:
                validate_builtin_profile_input_layout(
                    source_root,
                    config,
                    project_root=self.project_root,
                )
            manifest, discovery = build_manifest(
                task.id,
                source_root,
                task.config_hash,
                config,
                project_root=self.project_root,
            )
            start_index = 0
            counters = {key: 0 for key in COUNTER_KEYS}
            batch_index = 0
        else:
            cursor = latest.cursor
            manifest, discovery = self._load_checkpoint_manifest(
                cursor,
                source_root=source_root,
                config_hash=task.config_hash,
            )
            start_index = int(cursor.get("next_index", 0))
            counters = {
                key: int(dict(cursor.get("counts", {})).get(key, 0)) for key in COUNTER_KEYS
            }
            batch_index = latest.batch_index + 1

        if start_index < 0 or start_index > len(discovery.items):
            raise ValueError("Checkpoint next_index is outside the scan manifest")
        resumed_from = start_index
        active_paths = {item.relative_path for item in discovery.items}
        extracted_root = self.project_root / "data" / "tasks" / task.id / "extracted_frames"
        first_config_batch = not checkpoints

        ranges = list(range(start_index, len(discovery.items), config.batch_size))
        if not ranges and first_config_batch:
            ranges = [0]

        with ThreadPoolExecutor(
            max_workers=config.cpu_workers, thread_name_prefix="dataset-scan"
        ) as executor:
            for batch_start in ranges:
                batch_end = min(batch_start + config.batch_size, len(discovery.items))
                source_batch = discovery.items[batch_start:batch_end]
                scanned = tuple(
                    executor.map(
                        lambda item: self._scan_one(
                            item,
                            source_root=source_root,
                            config=config,
                            extracted_root=extracted_root,
                        ),
                        source_batch,
                    )
                )
                for item in scanned:
                    self._add_counts(counters, item)

                cursor = self._cursor(
                    manifest,
                    next_index=batch_end,
                    counters=counters,
                )

                def write_batch(
                    session,
                    *,
                    batch_items=scanned,
                    prepare=first_config_batch,
                ) -> None:
                    if prepare:
                        prepare_scan(session, task.id)
                        upsert_manifest_artifact(
                            session,
                            task_id=task.id,
                            config_hash=task.config_hash,
                            manifest=manifest,
                            project_root=self.project_root,
                        )
                    upsert_scanned_batch(
                        session,
                        task_id=task.id,
                        config_hash=task.config_hash,
                        active_paths=active_paths,
                        items=batch_items,
                        algorithm_version=SCAN_ALGORITHM_VERSION,
                    )

                try:
                    result = self.tasks.commit_batch(
                        token,
                        phase=TaskStatus.SCANNING,
                        config_hash=task.config_hash,
                        batch_index=batch_index,
                        completed_items=batch_end,
                        progress_total=len(discovery.items),
                        cursor=cursor,
                        lease_seconds=300,
                        batch_writer=write_batch,
                    )
                except StaleWorkerToken:
                    current = self.tasks.get_task(task.id)
                    return self._summary(
                        task.id,
                        manifest,
                        counters,
                        resumed_from,
                        current.status,
                    )
                first_config_batch = False
                batch_index += 1
                if result.control_state != "continue":
                    return self._summary(
                        task.id,
                        manifest,
                        counters,
                        resumed_from,
                        result.task.status,
                    )

        final_task = self._complete_or_honor_control(
            token,
            task_config_hash=task.config_hash,
            batch_index=batch_index,
            completed_items=len(discovery.items),
            cursor=self._cursor(
                manifest,
                next_index=len(discovery.items),
                counters=counters,
            ),
        )
        return self._summary(
            task.id,
            manifest,
            counters,
            resumed_from,
            final_task.status,
        )

    def finalize_precomputed_cpu_metrics(self, token: WorkerToken) -> str:
        task = self.tasks.get_task(token.task_id)
        if task.status != TaskStatus.CPU_METRICS.value:
            raise InvalidTaskTransition(
                f"CPU metric finalizer requires cpu_metrics status, got {task.status}"
            )
        checkpoints = [
            checkpoint
            for checkpoint in self.tasks.list_checkpoints(
                task.id, phase=TaskStatus.CPU_METRICS.value
            )
            if checkpoint.config_hash == task.config_hash
        ]
        with self.tasks.database.read_session() as session:
            sample_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(Sample)
                    .where(
                        Sample.task_id == task.id,
                        Sample.scan_state != "missing",
                    )
                )
                or 0
            )
            metric_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(Evidence)
                    .where(
                        Evidence.task_id == task.id,
                        Evidence.source == METRICS_ALGORITHM_VERSION,
                    )
                )
                or 0
            )
        next_batch_index = checkpoints[-1].batch_index + 1 if checkpoints else 0
        if not checkpoints:
            result = self.tasks.commit_batch(
                token,
                phase=TaskStatus.CPU_METRICS,
                config_hash=task.config_hash,
                batch_index=0,
                completed_items=sample_count,
                progress_total=sample_count,
                cursor={
                    "precomputed_during": TaskStatus.SCANNING.value,
                    "sample_count": sample_count,
                    "metric_count": metric_count,
                },
                lease_seconds=300,
            )
            if result.control_state != "continue":
                return result.task.status
            next_batch_index = 1
        try:
            return self.tasks.complete_phase(token, phase=TaskStatus.CPU_METRICS).status
        except StaleWorkerToken:
            return self.tasks.get_task(task.id).status
        except InvalidTaskTransition:
            current = self.tasks.get_task(task.id)
            if current.status not in {
                TaskStatus.PAUSING.value,
                TaskStatus.TERMINATING.value,
            }:
                raise
            result = self.tasks.commit_batch(
                token,
                phase=TaskStatus.CPU_METRICS,
                config_hash=task.config_hash,
                batch_index=next_batch_index,
                completed_items=sample_count,
                progress_total=sample_count,
                cursor={
                    "precomputed_during": TaskStatus.SCANNING.value,
                    "sample_count": sample_count,
                    "metric_count": metric_count,
                    "control_only": True,
                },
                lease_seconds=300,
            )
            return result.task.status

    def _scan_one(
        self,
        item: DiscoveredMedia,
        *,
        source_root: Path,
        config: ScanConfig,
        extracted_root: Path,
    ) -> ScannedMedia:
        try:
            decoded = decode_media(
                item,
                config,
                extracted_root=extracted_root,
                project_root=self.project_root,
            )
        except MediaDecodeError as error:
            state = "source_changed" if error.code == "source_changed" else "decode_error"
            evidence = (
                _scanner_evidence(
                    error.code,
                    error.detail,
                    severity="high",
                    metadata={"relative_path": item.relative_path},
                ),
            )
            return ScannedMedia(
                relative_path=item.relative_path,
                source_size=item.source_size,
                source_mtime_ns=item.source_mtime_ns,
                source_sha256=error.source_sha256 or UNREADABLE_SHA256,
                pixel_sha256=None,
                media_kind=item.media_kind_hint,
                artist_scope=item.artist_scope,
                scan_state=state,
                encoded_width=None,
                encoded_height=None,
                display_width=None,
                display_height=None,
                frame_count=None,
                is_animated=item.media_kind_hint != "image",
                exif_orientation=None,
                extracted_frame_path=None,
                export_requires_render=item.media_kind_hint != "image",
                phash=None,
                colorhash=None,
                evidence=evidence,
                resolutions=(),
            )

        try:
            pixel_hash = pixel_sha256(decoded.image)
            phash, colorhash = perceptual_hashes(decoded.image)
            metrics = calculate_metrics(decoded.image, config)
            resolutions = assess_resolutions(decoded.image.width, decoded.image.height, config)
            if decoded.source_changed:
                state = "source_changed"
            elif is_fully_transparent(metrics):
                state = "hard_reject"
                metrics += (
                    _scanner_evidence(
                        "fully_transparent_image",
                        True,
                        severity="high",
                    ),
                )
            else:
                state = "valid"
        except Exception as error:  # noqa: BLE001 - preserve the sample and evidence
            pixel_hash = None
            phash = None
            colorhash = None
            metrics = (
                _scanner_evidence(
                    "metric_error",
                    f"{type(error).__name__}: {error}",
                    severity="high",
                ),
            )
            resolutions = ()
            state = "metric_error"
        finally:
            display_width, display_height = decoded.image.size
            decoded.close()

        return ScannedMedia(
            relative_path=item.relative_path,
            source_size=item.source_size,
            source_mtime_ns=item.source_mtime_ns,
            source_sha256=decoded.source_sha256,
            pixel_sha256=pixel_hash,
            media_kind=decoded.media_kind,
            artist_scope=item.artist_scope,
            scan_state=state,
            encoded_width=decoded.encoded_width,
            encoded_height=decoded.encoded_height,
            display_width=display_width,
            display_height=display_height,
            frame_count=decoded.frame_count,
            is_animated=decoded.is_animated,
            exif_orientation=decoded.exif_orientation,
            extracted_frame_path=decoded.extracted_frame_path,
            export_requires_render=decoded.export_requires_render,
            phash=phash,
            colorhash=colorhash,
            evidence=tuple(metrics),
            resolutions=tuple(resolutions),
        )

    @staticmethod
    def _add_counts(counters: dict[str, int], item: ScannedMedia) -> None:
        counters["processed"] += 1
        if item.scan_state == "valid":
            counters["valid"] += 1
        elif item.scan_state == "hard_reject":
            counters["hard_rejected"] += 1
        elif item.scan_state in {"decode_error", "metric_error"}:
            counters["decode_errors"] += 1
        elif item.scan_state == "source_changed":
            counters["source_changed"] += 1

    def _cursor(
        self,
        manifest: ManifestInfo,
        *,
        next_index: int,
        counters: dict[str, int],
    ) -> dict[str, object]:
        return {
            "manifest_path": manifest.path.relative_to(self.project_root).as_posix(),
            "manifest_sha256": manifest.sha256,
            "next_index": next_index,
            "counts": dict(counters),
        }

    def _load_checkpoint_manifest(
        self,
        cursor: dict[str, object],
        *,
        source_root: Path,
        config_hash: str,
    ) -> tuple[ManifestInfo, DiscoveryResult]:
        relative_path = str(cursor.get("manifest_path", ""))
        expected_sha = str(cursor.get("manifest_sha256", ""))
        path = _manifest_from_cursor(relative_path, project_root=self.project_root)
        return load_manifest(
            path,
            source_root=source_root,
            expected_config_hash=config_hash,
            expected_sha256=expected_sha,
            project_root=self.project_root,
        )

    def _complete_or_honor_control(
        self,
        token: WorkerToken,
        *,
        task_config_hash: str,
        batch_index: int,
        completed_items: int,
        cursor: dict[str, object],
    ):
        current = self.tasks.get_task(token.task_id)
        if current.status in {
            TaskStatus.PAUSING.value,
            TaskStatus.TERMINATING.value,
        }:
            result = self.tasks.commit_batch(
                token,
                phase=TaskStatus.SCANNING,
                config_hash=task_config_hash,
                batch_index=batch_index,
                completed_items=completed_items,
                progress_total=completed_items,
                cursor={**cursor, "control_only": True},
                lease_seconds=300,
            )
            return result.task
        try:
            return self.tasks.complete_phase(token, phase=TaskStatus.SCANNING)
        except InvalidTaskTransition:
            current = self.tasks.get_task(token.task_id)
            if current.status in {
                TaskStatus.PAUSING.value,
                TaskStatus.TERMINATING.value,
            }:
                result = self.tasks.commit_batch(
                    token,
                    phase=TaskStatus.SCANNING,
                    config_hash=task_config_hash,
                    batch_index=batch_index,
                    completed_items=completed_items,
                    progress_total=completed_items,
                    cursor={**cursor, "control_only": True},
                    lease_seconds=300,
                )
                return result.task
            raise

    @staticmethod
    def _summary(
        task_id: str,
        manifest: ManifestInfo,
        counters: dict[str, int],
        resumed_from: int,
        final_status: str,
    ) -> ScanSummary:
        return ScanSummary(
            task_id=task_id,
            manifest_sha256=manifest.sha256,
            discovered=manifest.item_count,
            processed=counters["processed"],
            valid=counters["valid"],
            hard_rejected=counters["hard_rejected"],
            decode_errors=counters["decode_errors"],
            source_changed=counters["source_changed"],
            resumed_from_index=resumed_from,
            final_status=final_status,
        )
