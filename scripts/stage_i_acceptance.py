from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dataset_audit_studio.app.component_catalog import COMPONENT_REGISTRY
from dataset_audit_studio.captions.config import CaptionConfig
from dataset_audit_studio.clustering.config import ClusteringConfig, SelectionConfig
from dataset_audit_studio.database.models import ComponentRun
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.export.config import ExportConfig
from dataset_audit_studio.jobs.service import TaskService
from dataset_audit_studio.latent.config import LatentConfig
from dataset_audit_studio.latent.mikazuki import plan_mikazuki_namespace
from dataset_audit_studio.latent.types import LatentSample
from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.discovery import (
    MEDIA_EXTENSIONS,
    STATIC_IMAGE_EXTENSIONS,
    discover_media,
)
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.style.config import StyleConfig
from PIL import Image, ImageEnhance
from sqlalchemy import func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_STATUSES = {"completed", "failed", "terminated"}
REVIEW_GATES = {"awaiting_ai_review", "evidence_review"}
PHASES = (
    "scanning",
    "cpu_metrics",
    "model_scoring",
    "style_analysis",
    "semantic_clustering",
    "stage_selection",
    "exporting",
)


@dataclass(frozen=True)
class TreeSnapshot:
    identities: dict[str, tuple[int, int, str]]
    digest: str
    content_digest: str
    size_bytes: int


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_tree(root: Path) -> TreeSnapshot:
    root = root.resolve(strict=True)
    identities: dict[str, tuple[int, int, str]] = {}
    aggregate = hashlib.sha256()
    content_aggregate = hashlib.sha256()
    total = 0
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix().casefold(),
    ):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        digest = _sha256(path)
        identities[relative] = (stat.st_size, stat.st_mtime_ns, digest)
        total += stat.st_size
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(f"{stat.st_size}:{stat.st_mtime_ns}:{digest}".encode())
        aggregate.update(b"\n")
        content_aggregate.update(relative.encode("utf-8"))
        content_aggregate.update(b"\0")
        content_aggregate.update(f"{stat.st_size}:{digest}".encode())
        content_aggregate.update(b"\n")
    return TreeSnapshot(
        identities=identities,
        digest=aggregate.hexdigest(),
        content_digest=content_aggregate.hexdigest(),
        size_bytes=total,
    )


def _tree_size(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _require_project_path(path: Path, *, label: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        raise ValueError(f"{label} must stay inside the project: {resolved}") from None
    return resolved


def _write_report(path: Path, report: dict[str, Any]) -> None:
    target = _require_project_path(path, label="Report path")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _system_used_memory() -> tuple[int, int]:
    status = _MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return status.total_physical - status.available_physical, status.total_physical


def _gpu_snapshot() -> dict[str, Any] | None:
    """Read GPU memory via nvidia-smi.

    Each call spawns a process, so callers inside the polling loop must rate-limit it:
    sampling on every poll of a multi-hour run costs tens of thousands of process
    creations and perturbs the very timings this script exists to measure.
    """
    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    first = completed.stdout.strip().splitlines()[0]
    name, used, total = (value.strip() for value in first.split(",", 2))
    return {"name": name, "used_mib": int(used), "total_mib": int(total)}


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} {method} {path}: {detail}") from error


def _request_all_task_events(base_url: str, task_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    after = 0
    while True:
        page = _request_json(
            base_url,
            "GET",
            f"/api/tasks/{task_id}/events?after={after}&limit=1000",
        )
        items = page["items"]
        if not items:
            break
        events.extend(items)
        next_after = int(page["next_after"])
        if next_after <= after:
            raise RuntimeError("Task event pagination did not advance")
        after = next_after
        if len(items) < 1000:
            break
    return events


def _task_config(profile: str, caption_target: str) -> dict[str, Any]:
    enabled = profile == "benchmark"
    return {
        "scan": {
            "recursive": True,
            "resolutions": [512],
            "batch_size": 64,
            "cpu_workers": 12,
        },
        "scoring": {
            "enabled": enabled,
            "device": "cuda" if enabled else "auto",
            "precision": "float16" if enabled else "float32",
            "batch_size": 4,
            "aesthetic": {"enabled": enabled, "in_domain_threshold": 0.5},
            "ai": {"enabled": enabled},
            "ocr": {"enabled": enabled},
            "watermark": {"enabled": enabled},
        },
        "style": {
            "enabled": enabled,
            "device": "cuda" if enabled else "auto",
            "batch_size": 8,
        },
        "clustering": {
            "enabled": enabled,
            "scope_mode": "artist",
            "device": "cuda" if enabled else "auto",
            "embedding_batch_size": 16,
            "sae": {"enabled": False},
        },
        "selection": {
            "stages": [
                {
                    "aesthetic_minimum": 1.0 if not enabled else 1.5,
                    "maximum_ratio": 1.0 if not enabled else 0.8,
                    "technical_strictness": "fatal",
                },
                {
                    "aesthetic_minimum": 1.0 if not enabled else 2.5,
                    "maximum_ratio": 1.0 if not enabled else 0.5,
                    "technical_strictness": "high",
                },
                {
                    "aesthetic_minimum": 1.0 if not enabled else 3.5,
                    "maximum_ratio": 1.0 if not enabled else 0.2,
                    "technical_strictness": "medium",
                },
            ]
        },
        "caption": {"optimize": False, "target": caption_target},
        "export": {"batch_size": 64, "refuse_nonempty_output": True},
    }


def _event_phase_metrics(
    events: list[dict[str, Any]],
    sample_count: int,
    finished_at: str | None = None,
) -> dict[str, Any]:
    """Attribute wall-clock time between status events to the phase that was running.

    ``finished_at`` closes the final interval. Without it the span between the last
    recorded event and the end of the task belongs to no phase at all, which
    systematically under-reports whichever phase runs last -- normally ``exporting`` --
    and correspondingly inflates its images-per-second.
    """
    durations = {phase: 0.0 for phase in PHASES}
    current_status: str | None = None
    previous_time: datetime | None = None
    for event in events:
        current_time = datetime.fromisoformat(event["created_at"])
        if previous_time is not None and current_status in durations:
            durations[current_status] += (current_time - previous_time).total_seconds()
        current_status = event.get("to_status") or current_status
        previous_time = current_time

    if finished_at and previous_time is not None and current_status in durations:
        end_time = datetime.fromisoformat(finished_at)
        trailing = (end_time - previous_time).total_seconds()
        if trailing < 0:
            raise RuntimeError(
                f"Task finished at {finished_at}, before its last event at {previous_time}"
            )
        durations[current_status] += trailing

    return {
        phase: {
            "seconds": round(seconds, 6),
            "images_per_second": round(sample_count / seconds, 6) if seconds > 0 else None,
        }
        for phase, seconds in durations.items()
    }


def audit_legacy_compatibility(args: argparse.Namespace) -> int:
    database_path = _require_project_path(
        Path(args.database).resolve(strict=True),
        label="Database path",
    )
    if len(args.task_id) != len(args.task_report):
        raise ValueError("Each --task-id requires one matching --task-report")
    database_before = _sha256(database_path)
    database = Database(database_path)
    tasks = TaskService(database)
    audited: list[dict[str, Any]] = []
    try:
        for task_id, raw_report in zip(args.task_id, args.task_report, strict=True):
            baseline_path = _require_project_path(
                Path(raw_report).resolve(strict=True),
                label="Task report",
            )
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            if baseline.get("task_id") != task_id:
                raise RuntimeError(f"Task report does not belong to {task_id}")
            task = tasks.get_task(task_id)
            if task.status != "completed":
                raise RuntimeError(f"Legacy task {task_id} is not completed: {task.status}")
            if "components" in task.config:
                raise RuntimeError(f"Task {task_id} is not a legacy configuration")

            ScanConfig.from_task_config(task.config)
            ScoringConfig.from_task_config(task.config)
            StyleConfig.from_task_config(task.config)
            ClusteringConfig.from_task_config(task.config)
            SelectionConfig.from_task_config(task.config)
            CaptionConfig.from_task_config(task.config)
            LatentConfig.from_task_config(task.config)
            ExportConfig.from_task_config(task.config)
            normalized = COMPONENT_REGISTRY.normalize_task_config(task.config)
            resolved = COMPONENT_REGISTRY.resolve_task_config(task.config)

            source = _snapshot_tree(Path(task.source_root))
            output_path = Path(task.output_root or "")
            if not task.output_root or not output_path.is_dir():
                raise RuntimeError(f"Legacy task output is unavailable: {task.output_root}")
            output = _snapshot_tree(output_path)
            expected_source_digest = baseline["source"]["before_digest"]
            expected_output_files = sum(
                int(item["file_count"])
                for item in baseline.get("overview", {}).get("exports", [])
            )
            with database.read_session() as session:
                component_runs = int(
                    session.scalar(
                        select(func.count())
                        .select_from(ComponentRun)
                        .where(ComponentRun.task_id == task_id)
                    )
                    or 0
                )
            item = {
                "task_id": task_id,
                "name": task.name,
                "status": task.status,
                "config_hash": task.config_hash,
                "legacy_config_keys": sorted(task.config),
                "normalized_components": len(normalized),
                "resolved_components": [
                    entry.definition.manifest.id for entry in resolved
                ],
                "component_runs": component_runs,
                "source": {
                    "files": len(source.identities),
                    "size_bytes": source.size_bytes,
                    "digest": source.digest,
                    "expected_digest": expected_source_digest,
                    "matches_baseline": source.digest == expected_source_digest,
                },
                "output": {
                    "root": str(output_path.resolve(strict=True)),
                    "files": len(output.identities),
                    "size_bytes": output.size_bytes,
                    "digest": output.digest,
                    "content_digest": output.content_digest,
                    "expected_files": expected_output_files,
                    "matches_baseline_file_count": (
                        len(output.identities) == expected_output_files
                    ),
                },
                "baseline_report": str(baseline_path),
                "baseline_report_sha256": _sha256(baseline_path),
            }
            if not item["source"]["matches_baseline"]:
                raise RuntimeError(f"Legacy source digest changed for {task_id}")
            if not item["output"]["matches_baseline_file_count"]:
                raise RuntimeError(f"Legacy output file count changed for {task_id}")
            if len(normalized) != len(COMPONENT_REGISTRY.definitions):
                raise RuntimeError(f"Legacy config did not normalize completely for {task_id}")
            audited.append(item)
    finally:
        database.dispose()
    database_after = _sha256(database_path)
    if database_before != database_after:
        raise RuntimeError("Compatibility audit changed the database")

    report = {
        "kind": "j7_legacy_compatibility_audit",
        "database": str(database_path),
        "database_before_sha256": database_before,
        "database_after_sha256": database_after,
        "database_unchanged": True,
        "tasks": audited,
        "all_legacy_configs_readable": True,
        "all_sources_match": True,
        "all_outputs_readable": True,
    }
    _write_report(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def compare_output_trees(args: argparse.Namespace) -> int:
    baseline_root = _require_project_path(
        Path(args.baseline).resolve(strict=True),
        label="Baseline output",
    )
    candidate_root = _require_project_path(
        Path(args.candidate).resolve(strict=True),
        label="Candidate output",
    )
    baseline = _snapshot_tree(baseline_root)
    candidate = _snapshot_tree(candidate_root)
    baseline_content = {
        path: (identity[0], identity[2]) for path, identity in baseline.identities.items()
    }
    candidate_content = {
        path: (identity[0], identity[2]) for path, identity in candidate.identities.items()
    }
    missing = sorted(set(baseline_content) - set(candidate_content))
    extra = sorted(set(candidate_content) - set(baseline_content))
    changed = sorted(
        path
        for path in set(baseline_content) & set(candidate_content)
        if baseline_content[path] != candidate_content[path]
    )
    report = {
        "kind": "j7_output_equivalence",
        "baseline": {
            "root": str(baseline_root),
            "files": len(baseline.identities),
            "size_bytes": baseline.size_bytes,
            "content_digest": baseline.content_digest,
        },
        "candidate": {
            "root": str(candidate_root),
            "files": len(candidate.identities),
            "size_bytes": candidate.size_bytes,
            "content_digest": candidate.content_digest,
        },
        "missing": missing,
        "extra": extra,
        "changed": changed,
        "equivalent": baseline_content == candidate_content,
    }
    _write_report(Path(args.report), report)
    if not report["equivalent"]:
        raise RuntimeError(
            "Output trees differ: "
            f"missing={len(missing)}, extra={len(extra)}, changed={len(changed)}"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def compare_performance(args: argparse.Namespace) -> int:
    baseline_path = _require_project_path(
        Path(args.baseline_report).resolve(strict=True),
        label="Baseline performance report",
    )
    candidate_path = _require_project_path(
        Path(args.candidate_report).resolve(strict=True),
        label="Candidate performance report",
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if baseline["status"] != "completed" or candidate["status"] != "completed":
        raise RuntimeError("Performance reports must both describe completed tasks")
    if baseline["expected_images"] != candidate["expected_images"]:
        raise RuntimeError("Performance reports use different sample counts")
    if baseline["source"]["before_digest"] != candidate["source"]["before_digest"]:
        raise RuntimeError("Performance reports use different source content")

    phases: dict[str, Any] = {}
    for phase in PHASES:
        old = float(baseline["phase_metrics"][phase]["seconds"])
        new = float(candidate["phase_metrics"][phase]["seconds"])
        phases[phase] = {
            "baseline_seconds": old,
            "candidate_seconds": new,
            "delta_seconds": round(new - old, 6),
            "delta_percent": round((new - old) / old * 100, 6) if old else None,
            "candidate_images_per_second": candidate["phase_metrics"][phase][
                "images_per_second"
            ],
        }

    run_payload = _request_json(
        args.base_url,
        "GET",
        f"/api/components/runs/{candidate['task_id']}",
    )
    expected_images = int(candidate["expected_images"])
    if expected_images <= 0:
        raise RuntimeError(f"Candidate report has a non-positive sample count: {expected_images}")
    semantic_items = next(
        (
            int(item["completed_items"])
            for item in run_payload["items"]
            if item["component_id"] == "embedding.semantic"
        ),
        expected_images,
    )
    fallback_items = {
        "media.scan": expected_images,
        "metrics.technical": expected_images,
        "style.artist": expected_images,
        "cluster.hierarchy": semantic_items,
        "selection.three_stage": expected_images,
    }
    components: list[dict[str, Any]] = []
    for item in run_payload["items"]:
        started = item.get("started_at")
        finished = item.get("finished_at")
        seconds = None
        if started and finished:
            seconds = (
                datetime.fromisoformat(finished) - datetime.fromisoformat(started)
            ).total_seconds()
        completed_items = int(item["completed_items"])
        throughput_items = completed_items or fallback_items.get(item["component_id"], 0)
        components.append(
            {
                "component_id": item["component_id"],
                "status": item["status"],
                "execution": item["execution"],
                "model_ids": item["model_ids"],
                "seconds": round(seconds, 6) if seconds is not None else None,
                "completed_items": completed_items,
                "throughput_items": throughput_items,
                "throughput_basis": (
                    "component_checkpoint" if completed_items else "phase_input_fallback"
                ),
                "items_per_second": (
                    round(throughput_items / seconds, 6)
                    if seconds and throughput_items
                    else None
                ),
            }
        )

    def comparison(old: float, new: float) -> dict[str, float]:
        return {
            "baseline": old,
            "candidate": new,
            "delta": new - old,
            "delta_percent": (new - old) / old * 100 if old else 0.0,
        }

    total = comparison(
        float(baseline["elapsed_seconds"]),
        float(candidate["elapsed_seconds"]),
    )
    gpu = comparison(
        float(baseline["gpu"]["peak_growth_mib"]),
        float(candidate["gpu"]["peak_growth_mib"]),
    )
    memory = comparison(
        float(baseline["system_memory"]["peak_growth_bytes"]),
        float(candidate["system_memory"]["peak_growth_bytes"]),
    )
    report = {
        "kind": "j7_performance_comparison",
        "baseline_report": str(baseline_path),
        "candidate_report": str(candidate_path),
        "samples": expected_images,
        "source_digest": candidate["source"]["before_digest"],
        "total_seconds": total,
        "gpu_peak_growth_mib": gpu,
        "memory_peak_growth_bytes": memory,
        "pause": candidate["pause"],
        "phases": phases,
        "components": components,
        # Derived from the actual sample count. The previous hard-coded x100 silently
        # assumed every candidate run was exactly 1000 images, while --expected-images
        # has always been free to be anything.
        "linear_100k_estimate_hours": round(
            float(candidate["elapsed_seconds"]) / expected_images * 100_000 / 3600,
            6,
        ),
        "linear_100k_estimate_caveat": (
            "Straight-line extrapolation from "
            f"{expected_images} samples. Per-image phases (media.scan, metrics.technical, "
            "style.artist, embedding.semantic) extrapolate reasonably; hierarchical "
            "clustering is super-linear in the sample count, so the real 100k figure is a "
            "lower bound, not an estimate."
        ),
    }
    _write_report(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def audit_reference(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve(strict=True)
    started = time.perf_counter()
    before = _snapshot_tree(source)
    discovery = discover_media(
        source,
        ScanConfig(recursive=True, resolutions=(512,), batch_size=64, cpu_workers=12),
    )
    # MEDIA_EXTENSIONS is lower-case and discover_media matches it against a case-folded
    # suffix, so comparing the raw suffix here would drop every .JPG/.PNG that discovery
    # already accepted and fail the count assertions below for no visible reason.
    images = tuple(
        item
        for item in discovery.items
        if item.absolute_path.suffix.casefold() in MEDIA_EXTENSIONS
    )
    samples = []
    paired_json = 0
    for item in images:
        digest = before.identities[item.relative_path][2]
        if item.json_path is not None:
            paired_json += 1
        samples.append(
            LatentSample(
                sample_id=item.relative_path,
                relative_path=item.relative_path,
                source_path=item.absolute_path,
                source_size=item.source_size,
                source_mtime_ns=item.source_mtime_ns,
                source_sha256=digest,
                export_requires_render=False,
            )
        )
    plan = plan_mikazuki_namespace(
        source,
        tuple(samples),
        namespace=args.namespace,
        verified_sample_ids=frozenset(item.sample_id for item in samples),
    )
    catalog_entries = 0
    if plan.catalogs:
        catalog_entries = int(json.loads(plan.catalogs[0].content)["entry_count"])
    after = _snapshot_tree(source)
    unchanged = before.identities == after.identities
    report = {
        "kind": "stage_i_reference_audit",
        "source_root": str(source),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "source": {
            "files": len(before.identities),
            "size_bytes": before.size_bytes,
            "before_digest": before.digest,
            "after_digest": after.digest,
            "unchanged": unchanged,
        },
        "discovery": {
            "media": len(images),
            "paired_json": paired_json,
            "orphan_captions": discovery.orphan_caption_count,
            "pairing_collisions": sum(item.pairing_collision for item in images),
        },
        "latents": {
            "records": len(plan.records),
            "copy_files": len(plan.copies),
            "catalogs": len(plan.catalogs),
            "catalog_entries": catalog_entries,
        },
    }
    _write_report(Path(args.report), report)
    expected = args.expected_images
    if not unchanged:
        raise RuntimeError("Reference source changed during the audit")
    if len(images) != expected or paired_json != expected:
        raise RuntimeError(
            f"Reference counts differ: images={len(images)}, paired_json={paired_json}"
        )
    if len(plan.records) != expected or catalog_entries != expected:
        raise RuntimeError(
            f"Reference latent counts differ: records={len(plan.records)}, "
            f"catalog_entries={catalog_entries}"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _perturb_image(source_path: Path, target_path: Path, seed: int) -> bool:
    """Write a visually similar but byte-distinct variant of ``source_path``.

    Returns ``False`` when the file cannot be re-encoded (video, unsupported codec), so
    the caller can fall back to a verbatim copy and account for it honestly.

    The perturbation is deliberately mild -- a small crop, a small rescale, and a light
    brightness shift -- so the variant stays a plausible member of the same dataset while
    producing a distinct hash, a distinct perceptual hash and a distinct embedding.
    """
    if source_path.suffix.casefold() not in STATIC_IMAGE_EXTENSIONS:
        return False

    rng = random.Random(seed)
    try:
        with Image.open(source_path) as image:
            image.load()
            converted = image.convert("RGB") if image.mode not in ("RGB", "L") else image.copy()

        width, height = converted.size
        if width < 32 or height < 32:
            return False

        left = int(width * rng.uniform(0.01, 0.05))
        top = int(height * rng.uniform(0.01, 0.05))
        right = width - int(width * rng.uniform(0.01, 0.05))
        bottom = height - int(height * rng.uniform(0.01, 0.05))
        cropped = converted.crop((left, top, right, bottom))

        scale = rng.uniform(0.92, 1.0)
        resized = cropped.resize(
            (max(8, int(cropped.width * scale)), max(8, int(cropped.height * scale))),
            Image.Resampling.LANCZOS,
        )
        adjusted = ImageEnhance.Brightness(resized).enhance(rng.uniform(0.94, 1.06))

        target_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = target_path.suffix.casefold()
        if suffix in (".jpg", ".jpeg"):
            adjusted.save(target_path, quality=rng.randint(88, 96), subsampling=0)
        else:
            adjusted.save(target_path)
    except (OSError, ValueError):
        if target_path.exists():
            target_path.unlink()
        return False
    return True


def prepare_benchmark(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve(strict=True)
    destination = _require_project_path(Path(args.destination), label="Benchmark destination")
    if destination.exists():
        raise FileExistsError(f"Benchmark destination already exists: {destination}")
    before = _snapshot_tree(source)
    discovery = discover_media(
        source,
        ScanConfig(recursive=True, resolutions=(512,), batch_size=64, cpu_workers=12),
    )
    images = tuple(item for item in discovery.items if item.json_path is not None)
    if not images:
        raise RuntimeError("Benchmark source has no image and JSON pairs")
    artist = destination / args.artist_folder
    artist.mkdir(parents=True)
    started = time.perf_counter()

    verbatim = 0
    perturbed = 0
    fallback_duplicates = 0
    for index in range(args.count):
        item = images[index % len(images)]
        repeat = index // len(images)
        stem = f"sample_{index:06d}"
        target = artist / f"{stem}{item.absolute_path.suffix.casefold()}"
        # The first pass through the unique images is copied as-is; every later pass would
        # otherwise be a byte-identical duplicate. Duplicates are the worst possible
        # fixture for this project: content-level dedup collapses them, hierarchical
        # clustering degenerates on identical embeddings, and cache hit rates become
        # unrealistic -- so the measured cost of exactly the phases under test would not
        # reflect any real workload.
        if repeat == 0 or args.variant_mode == "copy":
            shutil.copy2(item.absolute_path, target)
            verbatim += 1
        elif _perturb_image(item.absolute_path, target, seed=index):
            perturbed += 1
        else:
            shutil.copy2(item.absolute_path, target)
            fallback_duplicates += 1
        assert item.json_path is not None
        shutil.copy2(item.json_path, artist / f"{stem}.json")

    after = _snapshot_tree(source)
    benchmark = _snapshot_tree(destination)
    image_digests = {
        identity[2]
        for relative, identity in benchmark.identities.items()
        if not relative.casefold().endswith(".json")
    }
    report = {
        "kind": "stage_i_benchmark_fixture",
        "source_root": str(source),
        "destination": str(destination),
        "samples": args.count,
        "unique_source_images": len(images),
        "variant_mode": args.variant_mode,
        "verbatim_copies": verbatim,
        "perturbed_variants": perturbed,
        "fallback_duplicate_copies": fallback_duplicates,
        "distinct_image_digests": len(image_digests),
        "duplicate_image_ratio": round(1 - len(image_digests) / args.count, 6),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "source_before_digest": before.digest,
        "source_after_digest": after.digest,
        "source_unchanged": before.identities == after.identities,
        "fixture_files": len(benchmark.identities),
        "fixture_size_bytes": benchmark.size_bytes,
        "fixture_digest": benchmark.digest,
    }
    if args.variant_mode == "copy":
        report["validity_warning"] = (
            "variant_mode=copy produces byte-identical duplicates. Deduplication, "
            "clustering and selection timings from this fixture do not extrapolate to a "
            "real dataset of the same size."
        )
    _write_report(Path(args.report), report)
    if not report["source_unchanged"]:
        raise RuntimeError("Reference source changed while preparing the benchmark")
    if len(benchmark.identities) != args.count * 2:
        raise RuntimeError("Benchmark fixture does not contain one image and JSON per sample")
    if args.variant_mode == "perturb" and fallback_duplicates:
        print(
            f"WARNING: {fallback_duplicates} sample(s) could not be perturbed and were "
            "copied verbatim; those are exact duplicates in the fixture.",
            file=sys.stderr,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def run_task(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve(strict=True)
    output = _require_project_path(Path(args.output), label="Task output")
    if output.exists():
        raise FileExistsError(f"Task output already exists: {output}")
    report_path = _require_project_path(Path(args.report), label="Report path")
    before_source = _snapshot_tree(source)
    before_disk = {
        "models": _tree_size(PROJECT_ROOT / "models"),
        "data": _tree_size(PROJECT_ROOT / "data"),
        "output": _tree_size(PROJECT_ROOT / "output"),
    }
    baseline_memory, total_memory = _system_used_memory()
    peak_memory = baseline_memory
    gpu = _gpu_snapshot()
    baseline_gpu = gpu["used_mib"] if gpu else None
    peak_gpu = baseline_gpu
    created = _request_json(
        args.base_url,
        "POST",
        "/api/tasks",
        {
            "name": args.name,
            "source_root": str(source),
            "output_root": str(output),
            "config": _task_config(args.profile, args.caption_target),
        },
    )
    task_id = created["id"]
    current = _request_json(
        args.base_url,
        "POST",
        f"/api/tasks/{task_id}/queue",
        {"expected_version": created["row_version"]},
    )
    started = time.perf_counter()
    deadline = started + args.timeout_seconds
    observed: list[dict[str, Any]] = []
    last_status = None
    phase_started = started
    pause_requested_at: float | None = None
    pause_response_seconds: float | None = None
    pause_completed = False

    last_gpu_sample = float("-inf")
    first_iteration = True

    while time.perf_counter() < deadline:
        # Sleeping at the top of the loop rather than the bottom keeps the control-flow
        # branches below from short-circuiting the delay. A `continue` after a review-gate
        # release, pause or resume used to skip the sleep entirely, so a status that had
        # not settled yet turned into a zero-delay request loop against the server.
        if not first_iteration:
            time.sleep(args.poll_seconds)
        first_iteration = False

        current = _request_json(args.base_url, "GET", f"/api/tasks/{task_id}")
        now = time.perf_counter()
        if current["status"] != last_status:
            last_status = current["status"]
            phase_started = now
            observed.append(
                {
                    "status": last_status,
                    "elapsed_seconds": round(now - started, 6),
                }
            )
        used_memory, _ = _system_used_memory()
        peak_memory = max(peak_memory, used_memory)
        if now - last_gpu_sample >= args.gpu_sample_seconds:
            last_gpu_sample = now
            current_gpu = _gpu_snapshot()
            if current_gpu is not None:
                peak_gpu = max(peak_gpu or current_gpu["used_mib"], current_gpu["used_mib"])

        if current["status"] in REVIEW_GATES:
            current = _request_json(
                args.base_url,
                "POST",
                f"/api/tasks/{task_id}/review-gate/release",
                {
                    "expected_gate": current["status"],
                    "expected_version": current["row_version"],
                },
            )
            continue
        if (
            args.pause_phase
            and not pause_completed
            and pause_requested_at is None
            and current["status"] == args.pause_phase
            and now - phase_started >= args.pause_after_seconds
        ):
            pause_requested_at = time.perf_counter()
            current = _request_json(
                args.base_url,
                "POST",
                f"/api/tasks/{task_id}/pause",
                {"expected_version": current["row_version"]},
            )
            continue
        if current["status"] == "paused" and pause_requested_at is not None:
            pause_response_seconds = time.perf_counter() - pause_requested_at
            pause_completed = True
            current = _request_json(
                args.base_url,
                "POST",
                f"/api/tasks/{task_id}/resume",
                {"expected_version": current["row_version"]},
            )
            continue
        if current["status"] in TERMINAL_STATUSES:
            break
    else:
        raise TimeoutError(f"Task did not finish in {args.timeout_seconds} seconds")

    events = _request_all_task_events(args.base_url, task_id)
    overview = _request_json(args.base_url, "GET", f"/api/tasks/{task_id}/overview")
    after_source = _snapshot_tree(source)
    after_disk = {
        "models": _tree_size(PROJECT_ROOT / "models"),
        "data": _tree_size(PROJECT_ROOT / "data"),
        "output": _tree_size(PROJECT_ROOT / "output"),
    }
    report = {
        "kind": "stage_i_task_benchmark",
        "profile": args.profile,
        "task_id": task_id,
        "task_name": args.name,
        "status": current["status"],
        "error_code": current.get("error_code"),
        "error_message": current.get("error_message"),
        "source_root": str(source),
        "output_root": str(output),
        "expected_images": args.expected_images,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "observed_statuses": observed,
        "phase_metrics": _event_phase_metrics(
            events,
            args.expected_images,
            current.get("finished_at") or current.get("updated_at"),
        ),
        "pause": {
            "requested_phase": args.pause_phase,
            "completed": pause_completed,
            "response_seconds": (
                round(pause_response_seconds, 6)
                if pause_response_seconds is not None
                else None
            ),
        },
        "gpu": {
            "name": gpu["name"] if gpu else None,
            "total_mib": gpu["total_mib"] if gpu else None,
            "baseline_used_mib": baseline_gpu,
            "peak_used_mib": peak_gpu,
            "peak_growth_mib": (
                peak_gpu - baseline_gpu
                if peak_gpu is not None and baseline_gpu is not None
                else None
            ),
        },
        "system_memory": {
            "total_bytes": total_memory,
            "baseline_used_bytes": baseline_memory,
            "peak_used_bytes": peak_memory,
            "peak_growth_bytes": peak_memory - baseline_memory,
        },
        "disk": {
            key: {
                "before_bytes": before_disk[key],
                "after_bytes": after_disk[key],
                "growth_bytes": after_disk[key] - before_disk[key],
            }
            for key in before_disk
        },
        "source": {
            "files": len(before_source.identities),
            "size_bytes": before_source.size_bytes,
            "before_digest": before_source.digest,
            "after_digest": after_source.digest,
            "unchanged": before_source.identities == after_source.identities,
        },
        "overview": overview,
        "event_count": len(events),
    }
    _write_report(report_path, report)
    if current["status"] != "completed":
        raise RuntimeError(
            f"Acceptance task ended as {current['status']}: {current.get('error_message')}"
        )
    if not report["source"]["unchanged"]:
        raise RuntimeError("Task changed its source dataset")
    if args.pause_phase and not pause_completed:
        raise RuntimeError(f"Task never completed a pause during {args.pause_phase}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def refresh_task_report(args: argparse.Namespace) -> int:
    report_path = _require_project_path(
        Path(args.report).resolve(strict=True),
        label="Task report",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    task_id = str(report["task_id"])
    task = _request_json(args.base_url, "GET", f"/api/tasks/{task_id}")
    events = _request_all_task_events(args.base_url, task_id)
    overview = _request_json(args.base_url, "GET", f"/api/tasks/{task_id}/overview")
    report.update(
        {
            "status": task["status"],
            "error_code": task.get("error_code"),
            "error_message": task.get("error_message"),
            "phase_metrics": _event_phase_metrics(
                events,
                int(report["expected_images"]),
                task.get("finished_at") or task.get("updated_at"),
            ),
            "overview": overview,
            "event_count": len(events),
        }
    )
    _write_report(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Stage I acceptance checks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compatibility = subparsers.add_parser("audit-legacy-compatibility")
    compatibility.add_argument("--database", required=True)
    compatibility.add_argument("--task-id", action="append", required=True)
    compatibility.add_argument("--task-report", action="append", required=True)
    compatibility.add_argument("--report", required=True)
    compatibility.set_defaults(handler=audit_legacy_compatibility)

    compare = subparsers.add_parser("compare-output")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--report", required=True)
    compare.set_defaults(handler=compare_output_trees)

    performance = subparsers.add_parser("compare-performance")
    performance.add_argument("--baseline-report", required=True)
    performance.add_argument("--candidate-report", required=True)
    performance.add_argument("--report", required=True)
    performance.add_argument("--base-url", default="http://127.0.0.1:7865")
    performance.set_defaults(handler=compare_performance)

    reference = subparsers.add_parser("audit-reference")
    reference.add_argument("--source", required=True)
    reference.add_argument("--report", required=True)
    # Required rather than defaulted: this is the assertion the whole subcommand turns on,
    # and a default carried over from one particular reference directory would silently
    # pass or fail against any other one.
    reference.add_argument("--expected-images", type=int, required=True)
    reference.add_argument("--namespace", default="anima")
    reference.set_defaults(handler=audit_reference)

    fixture = subparsers.add_parser("prepare-benchmark")
    fixture.add_argument("--source", required=True)
    fixture.add_argument("--destination", required=True)
    fixture.add_argument("--report", required=True)
    fixture.add_argument("--count", type=int, default=1000)
    fixture.add_argument("--artist-folder", default="alaskanya")
    fixture.add_argument(
        "--variant-mode",
        choices=("perturb", "copy"),
        default="perturb",
        help="perturb (default) writes mildly distinct variants once the unique source "
        "images run out; copy reproduces the old byte-identical duplicates and produces "
        "a fixture whose dedup, clustering and selection timings do not extrapolate",
    )
    fixture.set_defaults(handler=prepare_benchmark)

    task = subparsers.add_parser("run-task")
    task.add_argument("--source", required=True)
    task.add_argument("--output", required=True)
    task.add_argument("--report", required=True)
    task.add_argument("--name", required=True)
    task.add_argument("--profile", choices=("reference", "benchmark"), required=True)
    task.add_argument("--expected-images", type=int, required=True)
    task.add_argument("--base-url", default="http://127.0.0.1:7865")
    task.add_argument("--pause-phase", choices=PHASES)
    task.add_argument("--pause-after-seconds", type=float, default=2.0)
    task.add_argument("--poll-seconds", type=float, default=0.5)
    task.add_argument("--caption-target", default="anima")
    task.add_argument(
        "--gpu-sample-seconds",
        type=float,
        default=5.0,
        help="minimum interval between nvidia-smi samples; each sample spawns a process, "
        "so sampling on every poll perturbs the timings being measured",
    )
    task.add_argument("--timeout-seconds", type=float, default=14_400)
    task.set_defaults(handler=run_task)

    refresh = subparsers.add_parser("refresh-task-report")
    refresh.add_argument("--report", required=True)
    refresh.add_argument("--base-url", default="http://127.0.0.1:7865")
    refresh.set_defaults(handler=refresh_task_report)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
