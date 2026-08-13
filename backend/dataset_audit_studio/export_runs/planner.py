from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from base64 import b64decode, b64encode
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select

from dataset_audit_studio.components.dataset_export.annotations import plan_paired_annotations
from dataset_audit_studio.components.dataset_export.contracts import (
    DatasetSummary,
    ExportPlan,
    PlannedFile,
)
from dataset_audit_studio.core.dataset_artifacts import (
    DatasetSample,
)
from dataset_audit_studio.core.file_integrity import file_identity_matches, sha256_file
from dataset_audit_studio.database.models import (
    Evidence,
    ExportRun,
    Sample,
    Task,
    TaskConfig,
)
from dataset_audit_studio.database.session import Database
from dataset_audit_studio.export.image_conversion import (
    ImageExportFormat,
    normalize_image_format,
    output_suffix,
)
from dataset_audit_studio.export_runs.eligibility import ELIGIBILITY_REASONS, EligibilityResolver
from dataset_audit_studio.export_runs.errors import ExportRunError
from dataset_audit_studio.export_runs.transcode_cache import cached_encode_export_image
from dataset_audit_studio.export_runs.types import ExportRunPreview
from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scoring.assets import EVIDENCE_SOURCES, PREPROCESSING_VERSIONS
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.workspace.curated import AestheticScoreRecord, resolve_aesthetic_score

_SUMMARY_REASONS = ELIGIBILITY_REASONS


@dataclass(frozen=True)
class RunPlan:
    plan: ExportPlan
    summary: dict[str, dict[str, dict[str, int]] | dict[str, int]]
    preview_digest: str = ""
    input_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True)
class _PendingExportFile:
    destination_relative: str
    kind: str
    source_path: Path
    transcode_format: str | None = None
    conversion_relative_path: str | None = None


@dataclass(frozen=True)
class _PendingRunPlan:
    task_id: str
    task_config_revision: int
    config_hash: str
    settings: dict[str, Any]
    source_root: Path
    eligibility_digest: str
    eligibility_evidence: dict[str, Any]
    duplicate_groups: tuple[dict[str, Any], ...]
    folder_summaries: tuple[dict[str, Any], ...]
    exclusion_counts: dict[str, int]
    included_count: int
    folder_below_minimum: dict[str, int]
    warnings: tuple[str, ...]
    canonical_sample_ids: list[str]
    dataset_roots: tuple[str, ...]
    pending_files: tuple[_PendingExportFile, ...]
    annotation_requests: tuple[tuple[Path, PurePosixPath], ...]


class ExportRunPlanner:
    """Build a deterministic copy plan without writing task-owned records."""

    def __init__(self, database: Database, *, project_root: Path | None = None) -> None:
        self.database = database
        self.project_root = project_root.resolve(strict=False) if project_root else None

    def build(self, export_run_id: str) -> RunPlan:
        with self.database.read_session() as session:
            run = session.get(ExportRun, export_run_id)
            if run is None:
                raise ExportRunError("export_input_changed", "Export run no longer exists")
            task = session.get(Task, run.task_id)
            if task is None:
                raise ExportRunError("export_input_changed", "Export task no longer exists")
            config = session.scalar(
                select(TaskConfig).where(
                    TaskConfig.task_id == task.id,
                    TaskConfig.revision == task.current_config_revision,
                )
            )
            if (
                config is None
                or task.current_config_revision != run.task_config_revision
                or config.config_hash != run.config_hash
                or run.selection_version != task.current_config_revision
            ):
                raise ExportRunError("export_input_changed", "Export run input snapshot changed")
            snapshot = run.input_snapshot_json
            if not isinstance(snapshot, dict):
                raise ExportRunError(
                    "export_legacy_payload_unsupported",
                    "Export run has no immutable input snapshot",
                )
            return self._build_snapshot(task, run, snapshot, self._run_settings(run))

    def preview(
        self,
        task_id: str,
        *,
        minimum_resolution: int,
        domain_minimum: float | None,
        exclude_exact_visual_duplicates: bool,
        style_outlier_mode: str,
        aesthetic_minimum: float | None,
        minimum_folder_images: int,
        add_repeat_prefix: bool,
        sample_seen_mode: str,
        sample_seen_target: int | None,
        image_format: str = "original",
    ) -> ExportRunPreview:
        with self.database.read_session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ExportRunError("task_not_found", f"Task not found: {task_id}")
            config = session.scalar(
                select(TaskConfig).where(
                    TaskConfig.task_id == task.id,
                    TaskConfig.revision == task.current_config_revision,
                )
            )
            if config is None or not ExportRunPlanner._has_builtin_profile(config.config_json):
                raise ExportRunError(
                    "legacy_task_config_unsupported",
                    "Profile-free task configuration is no longer supported",
                )
            if task.status not in {"completed", "evidence_review"}:
                raise ExportRunError(
                    "export_task_not_completed", "Only completed tasks can preview an export run"
                )
            export = (
                config.config_json.get("export") if isinstance(config.config_json, dict) else {}
            )
            if not isinstance(export, dict):
                export = {}
            pending = self.plan_current(
                session,
                task,
                config,
                {
                    "schema": "export.run.settings.v3",
                    "mode": "copy",
                    "minimum_resolution": minimum_resolution,
                    "domain_minimum": domain_minimum,
                    "exclude_exact_visual_duplicates": exclude_exact_visual_duplicates,
                    "style_outlier_mode": style_outlier_mode,
                    "aesthetic_minimum": aesthetic_minimum,
                    "minimum_folder_images": minimum_folder_images,
                    "add_repeat_prefix": add_repeat_prefix,
                    "sample_seen_mode": sample_seen_mode,
                    "sample_seen_target": sample_seen_target,
                    "image_format": normalize_image_format(image_format),
                    "keep_annotation_files": bool(export.get("keep_annotation_files", True)),
                },
            )
        planned = self.finalize_plan(pending)
        summary = planned.summary
        return ExportRunPreview(
            task_id=task_id,
            minimum_resolution=minimum_resolution,
            domain_minimum=domain_minimum,
            exclude_exact_visual_duplicates=exclude_exact_visual_duplicates,
            style_outlier_mode=style_outlier_mode,
            aesthetic_minimum=aesthetic_minimum,
            minimum_folder_images=minimum_folder_images,
            add_repeat_prefix=add_repeat_prefix,
            sample_seen_mode=sample_seen_mode,
            sample_seen_target=sample_seen_target,
            preview_digest=planned.preview_digest,
            input_digest=planned.plan.input_digest,
            eligibility_digest=str(summary["eligibility_digest"]),
            settings=dict(summary["settings"]),
            included_count=int(summary.get("included_count", 0)),
            exclusion_counts=dict(summary.get("exclusion_counts", {})),
            folder_below_minimum=dict(summary.get("folder_below_minimum", {})),
            folders=tuple(summary.get("folders", ())),
            duplicate_groups=tuple(summary.get("duplicate_groups", ())),
            warnings=tuple(summary.get("warnings", ())),
        )

    @staticmethod
    def _run_settings(run: ExportRun) -> dict[str, Any]:
        if list(run.resolutions_json or []) != [run.minimum_resolution]:
            raise ExportRunError(
                "export_legacy_payload_unsupported",
                "Multi-resolution export run payload is unsupported",
            )
        if (
            isinstance(run.minimum_folder_images, bool)
            or not isinstance(run.minimum_folder_images, int)
            or run.minimum_folder_images <= 0
        ):
            raise ExportRunError(
                "export_minimum_folder_images_invalid",
                "Export run folder threshold is invalid",
            )
        if run.sample_seen_mode not in {"off", "auto", "manual"}:
            raise ExportRunError(
                "export_sample_seen_mode_invalid", "Export run sample-seen mode is invalid"
            )
        if run.sample_seen_mode != "off" and run.add_repeat_prefix is not True:
            raise ExportRunError(
                "export_repeat_prefix_required",
                "Sample-seen balancing requires add_repeat_prefix",
            )
        if run.sample_seen_mode == "manual":
            if (
                isinstance(run.sample_seen_target, bool)
                or not isinstance(run.sample_seen_target, int)
                or run.sample_seen_target <= 0
            ):
                raise ExportRunError(
                    "export_sample_seen_target_invalid",
                    "Export run sample-seen target is invalid",
                )
        elif run.sample_seen_target is not None:
            raise ExportRunError(
                "export_sample_seen_target_invalid",
                "Export run sample-seen target is invalid",
            )
        settings = run.settings_json if isinstance(run.settings_json, dict) else None
        if settings is None or settings.get("schema") != "export.run.settings.v3":
            raise ExportRunError(
                "export_legacy_payload_unsupported",
                "Export run settings schema is unsupported",
            )
        if (
            settings.get("mode") != "copy"
            or settings.get("minimum_resolution") != run.minimum_resolution
            or settings.get("aesthetic_minimum") != run.aesthetic_minimum
            or settings.get("minimum_folder_images") != run.minimum_folder_images
            or settings.get("add_repeat_prefix") is not run.add_repeat_prefix
            or settings.get("sample_seen_mode") != run.sample_seen_mode
            or settings.get("sample_seen_target") != run.sample_seen_target
            or not isinstance(settings.get("keep_annotation_files"), bool)
        ):
            raise ExportRunError("export_input_changed", "Export run settings changed")
        if settings.get("image_format", "original") not in {"original", "jpeg", "png", "webp"}:
            raise ExportRunError(
                "export_image_format_invalid", "Export run image format is invalid"
            )
        domain = settings.get("domain_minimum")
        if (
            domain is not None
            and (
                isinstance(domain, bool)
                or not isinstance(domain, (int, float))
                or not math.isfinite(float(domain))
                or not 0.0 <= float(domain) <= 1.0
            )
        ):
            raise ExportRunError("export_domain_minimum_invalid", "Domain minimum is invalid")
        duplicates = settings.get("exclude_exact_visual_duplicates")
        if not isinstance(duplicates, bool):
            raise ExportRunError(
                "export_duplicate_filter_invalid", "Duplicate filter must be boolean"
            )
        style_mode = settings.get("style_outlier_mode")
        if style_mode not in {"off", "strong", "all"}:
            raise ExportRunError("export_style_outlier_mode_invalid", "Style mode is invalid")
        settings["image_format"] = normalize_image_format(settings.get("image_format", "original"))
        return dict(settings)

    def _build_current(
        self, session, task: Task, config: TaskConfig, settings: dict[str, Any]
    ) -> RunPlan:
        return self.finalize_plan(self.plan_current(session, task, config, settings))

    def plan_current(
        self, session, task: Task, config: TaskConfig, settings: dict[str, Any]
    ) -> _PendingRunPlan:
        source_root = Path(task.source_root).resolve(strict=True)
        rows = session.scalars(
            select(Sample)
            .where(Sample.task_id == task.id, Sample.scan_state == "valid")
            .order_by(Sample.relative_path, Sample.id)
        ).all()
        eligibility = EligibilityResolver().resolve(
            session,
            task=task,
            config=config,
            rows=rows,
            settings=settings,
        )
        total = dict(eligibility.exclusion_counts)
        folders: dict[str, dict[str, Any]] = {}
        retained: list[tuple[Sample, str]] = []
        for row in rows:
            if eligibility.outcomes[row.id].reason is not None:
                continue
            source_identifier, suffix = self._source_folder(row.relative_path, source_root.name)
            key = self._folder_key(source_identifier)
            folder = folders.setdefault(
                key, {"source_identifier": source_identifier, "rows": [], "suffixes": {}}
            )
            if folder["source_identifier"] != source_identifier:
                raise ExportRunError(
                    "export_collision", "Source folders collide after normalization"
                )
            folder["rows"].append(row)
            folder["suffixes"][row.id] = suffix
        min_images = settings["minimum_folder_images"]
        below_folders: list[dict[str, Any]] = []
        for folder in folders.values():
            count = len(folder["rows"])
            if count < min_images:
                total["folder_below_minimum"] += count
                total["included"] -= count
                folder["excluded"] = True
                folder["exclusion_reason"] = "folder_below_minimum"
                below_folders.append(folder)
            else:
                retained.extend((row, folder["source_identifier"]) for row in folder["rows"])

        folder_items = list(folders.values())
        target = None
        mode = settings["sample_seen_mode"]
        if mode == "auto" and retained:
            target = max(
                self._parse_repeat(folder["source_identifier"])[0] * len(folder["rows"])
                for folder in folder_items
                if not folder.get("excluded")
            )
        elif mode == "manual":
            target = settings["sample_seen_target"]
        output_names: dict[str, str] = {}
        for folder in sorted(
            folder_items,
            key=lambda item: (item["source_identifier"].casefold(), item["source_identifier"]),
        ):
            source_identifier = folder["source_identifier"]
            original_repeat, base = self._parse_repeat(source_identifier)
            count = len(folder["rows"])
            original_seen = original_repeat * count
            new_repeat = original_repeat
            warning_codes: list[str] = []
            if mode in {"auto", "manual"} and not folder.get("excluded"):
                assert target is not None
                if original_seen > target:
                    warning_codes.append("sample_seen_original_above_target")
                else:
                    new_repeat = max(
                        original_repeat, self._closest_repeat(target, count, original_repeat)
                    )
                    if new_repeat * count != target:
                        warning_codes.append("sample_seen_approximate")
                    if new_repeat * count > target:
                        warning_codes.append("sample_seen_target_exceeded")
            if settings["sample_seen_mode"] in {"auto", "manual"}:
                output_name = f"{new_repeat}_{base}"
            elif settings["add_repeat_prefix"] and not self._has_repeat_prefix(source_identifier):
                output_name = f"1_{base}"
            else:
                output_name = source_identifier
            normalized = self._folder_key(output_name)
            if normalized in output_names and output_names[normalized] != source_identifier:
                raise ExportRunError("export_collision", "Output folders collide after mapping")
            output_names[normalized] = source_identifier
            folder.update(
                {
                    "output_folder": output_name,
                    "image_count": count,
                    "original_repeat": original_repeat,
                    "new_repeat": new_repeat,
                    "original_sample_seen": original_seen,
                    "new_sample_seen": new_repeat * count,
                    "exclusion_reason": folder.get("exclusion_reason"),
                    "warning_codes": warning_codes,
                }
            )
        if settings["sample_seen_mode"] == "auto" and target is None:
            target = 0
        pending_files: list[_PendingExportFile] = []
        annotation_requests: list[tuple[Path, PurePosixPath]] = []
        dataset_roots: list[str] = []
        image_format = normalize_image_format(settings.get("image_format", "original"))
        used_converted_paths: set[str] = set()
        for folder in sorted(
            folder_items, key=lambda item: (item["output_folder"].casefold(), item["output_folder"])
        ):
            if folder.get("excluded"):
                continue
            output_folder = folder["output_folder"]
            dataset_roots.append(output_folder)
            for row in sorted(
                folder["rows"],
                key=lambda item: (item.relative_path.casefold(), item.relative_path, item.id),
            ):
                sample = self._sample(task, row)
                if not file_identity_matches(
                    sample.source_path,
                    size_bytes=sample.source_size,
                    mtime_ns=sample.source_mtime_ns,
                    sha256=sample.source_sha256,
                ):
                    raise ExportRunError(
                        "export_source_hash_mismatch",
                        f"Source identity changed: {row.relative_path}",
                    )
                source = sample.image_path if sample.export_requires_render else sample.source_path
                relative = PurePosixPath(output_folder, *folder["suffixes"][row.id])
                if sample.export_requires_render:
                    relative = relative.with_suffix(".png")
                if image_format == "original":
                    pending_files.append(
                        _PendingExportFile(
                            relative.as_posix(),
                            "rendered_image" if sample.export_requires_render else "source_image",
                            source,
                        )
                    )
                else:
                    relative = self._converted_destination(
                        relative,
                        source_relative=PurePosixPath(*folder["suffixes"][row.id]),
                        image_format=image_format,
                        used_paths=used_converted_paths,
                        sample_id=row.id,
                    )
                    pending_files.append(
                        _PendingExportFile(
                            relative.as_posix(),
                            "converted_image",
                            source,
                            transcode_format=image_format,
                            conversion_relative_path=row.relative_path,
                        )
                    )
                if settings.get("keep_annotation_files", True):
                    annotation_requests.append((sample.source_path, relative))
        folder_summaries = tuple(
            {key: value for key, value in folder.items() if key not in {"rows", "suffixes"}}
            for folder in sorted(
                folder_items,
                key=lambda item: (item["source_identifier"].casefold(), item["source_identifier"]),
            )
        )
        included_count = sum(
            len(folder["rows"]) for folder in folder_items if not folder.get("excluded")
        )
        warnings = tuple(
            sorted(
                {
                    *eligibility.warnings,
                    *(code for folder in folder_items for code in folder.get("warning_codes", [])),
                }
            )
        )
        return _PendingRunPlan(
            task_id=task.id,
            task_config_revision=task.current_config_revision,
            config_hash=config.config_hash,
            settings=dict(settings),
            source_root=source_root,
            eligibility_digest=eligibility.eligibility_digest,
            eligibility_evidence=eligibility.evidence_provenance,
            duplicate_groups=eligibility.duplicate_groups,
            folder_summaries=folder_summaries,
            exclusion_counts=dict(total),
            included_count=included_count,
            folder_below_minimum={
                "folder_count": len(below_folders),
                "image_count": sum(len(folder["rows"]) for folder in below_folders),
            },
            warnings=warnings,
            canonical_sample_ids=[row.id for row, _ in retained],
            dataset_roots=tuple(dataset_roots),
            pending_files=tuple(pending_files),
            annotation_requests=tuple(annotation_requests),
        )

    def finalize_plan(self, pending: _PendingRunPlan) -> RunPlan:
        files: list[PlannedFile] = []
        for item in pending.pending_files:
            if item.transcode_format is not None:
                try:
                    encoded = cached_encode_export_image(item.source_path, item.transcode_format)
                except (OSError, ValueError) as error:
                    raise ExportRunError(
                        "export_image_conversion_failed",
                        f"Unable to convert image: {item.conversion_relative_path}",
                    ) from error
                files.append(
                    PlannedFile(
                        item.destination_relative,
                        hashlib.sha256(encoded).hexdigest(),
                        len(encoded),
                        item.kind,
                        source_path=item.source_path,
                        transcode_format=item.transcode_format,
                    )
                )
            else:
                files.append(
                    PlannedFile(
                        item.destination_relative,
                        sha256_file(item.source_path),
                        item.source_path.stat().st_size,
                        item.kind,
                        source_path=item.source_path,
                    )
                )
        for image_source, destination_image in pending.annotation_requests:
            files.extend(
                plan_paired_annotations(
                    image_source=image_source,
                    source_root=pending.source_root,
                    destination_image=destination_image,
                )
            )
        files = self._dedupe_files(files)
        minimum = pending.settings["minimum_resolution"]
        datasets = tuple(
            DatasetSummary(
                stage=1,
                resolution=minimum,
                relative_root=output_folder,
                file_count=sum(
                    1
                    for item in files
                    if item.destination_relative == output_folder
                    or item.destination_relative.startswith(output_folder + "/")
                ),
                byte_count=sum(
                    item.size_bytes
                    for item in files
                    if item.destination_relative == output_folder
                    or item.destination_relative.startswith(output_folder + "/")
                ),
            )
            for output_folder in pending.dataset_roots
        )
        summary = {
            "schema": "export.run.summary.v3",
            "settings": dict(pending.settings),
            "eligibility_digest": pending.eligibility_digest,
            "eligibility_evidence": pending.eligibility_evidence,
            "included_count": pending.included_count,
            "exclusion_counts": dict(pending.exclusion_counts),
            "folder_below_minimum": dict(pending.folder_below_minimum),
            "folders": pending.folder_summaries,
            "duplicate_groups": pending.duplicate_groups,
            "warnings": pending.warnings,
        }
        digest_payload = {
            "contract": "export.run.v3",
            "task_id": pending.task_id,
            "task_config_revision": pending.task_config_revision,
            "config_hash": pending.config_hash,
            "settings": summary["settings"],
            "eligibility_digest": pending.eligibility_digest,
            "duplicate_groups": pending.duplicate_groups,
            "folders": pending.folder_summaries,
            "exclusion_counts": pending.exclusion_counts,
            "files": [
                [
                    item.destination_relative,
                    item.sha256,
                    item.size_bytes,
                    item.kind,
                    item.transcode_format,
                ]
                for item in files
            ],
        }
        digest_bytes = json.dumps(
            digest_payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        preview_digest = hashlib.sha256(b"preview:" + digest_bytes).hexdigest()
        input_digest = hashlib.sha256(b"input:" + digest_bytes).hexdigest()
        summary["preview_digest"] = preview_digest
        summary["input_digest"] = input_digest
        input_snapshot = {
            "schema": "export.run.input.v2",
            "input_digest": input_digest,
            "preview_digest": preview_digest,
            "eligibility_digest": pending.eligibility_digest,
            "settings": dict(pending.settings),
            "canonical_sample_ids": list(pending.canonical_sample_ids),
            "summary": summary,
            "files": [
                {
                    "destination_relative": item.destination_relative,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                    "kind": item.kind,
                    "source_ref": (
                        self._snapshot_source_ref(item.source_path, source_root=pending.source_root)
                        if item.source_path is not None
                        else None
                    ),
                    "content_base64": (
                        b64encode(item.content).decode("ascii")
                        if item.content is not None
                        else None
                    ),
                    "transcode_format": item.transcode_format,
                }
                for item in files
            ],
        }
        return RunPlan(
            plan=ExportPlan(
                files=tuple(files),
                datasets=datasets,
                latent_records=(),
                input_digest=input_digest,
            ),
            summary=summary,
            preview_digest=preview_digest,
            input_snapshot=input_snapshot,
        )

    @staticmethod
    def plan_fingerprint(pending: _PendingRunPlan) -> str:
        # Compare DB-derived inputs only; encoded bytes are produced outside the write lock.
        payload = {
            "eligibility_digest": pending.eligibility_digest,
            "canonical_sample_ids": pending.canonical_sample_ids,
            "settings": pending.settings,
            "files": [
                [
                    item.destination_relative,
                    item.kind,
                    item.source_path.as_posix(),
                    item.transcode_format,
                ]
                for item in pending.pending_files
            ],
            "annotations": [
                [source.as_posix(), destination.as_posix()]
                for source, destination in pending.annotation_requests
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    def _build_snapshot(
        self,
        task: Task,
        run: ExportRun,
        snapshot: dict[str, Any],
        settings: dict[str, Any],
    ) -> RunPlan:
        if snapshot.get("schema") != "export.run.input.v2":
            raise ExportRunError(
                "export_legacy_payload_unsupported",
                "Export input snapshot schema is unsupported",
            )
        input_digest = snapshot.get("input_digest")
        preview_digest = snapshot.get("preview_digest")
        if (
            not isinstance(input_digest, str)
            or input_digest != run.input_digest
            or not isinstance(preview_digest, str)
            or preview_digest != run.preview_digest
        ):
            raise ExportRunError("export_input_changed", "Export input snapshot identity changed")
        summary = snapshot.get("summary")
        entries = snapshot.get("files")
        eligibility_digest = snapshot.get("eligibility_digest")
        snapshot_settings = snapshot.get("settings")
        summary_settings = summary.get("settings") if isinstance(summary, dict) else None
        if isinstance(snapshot_settings, dict) and "image_format" not in snapshot_settings:
            snapshot_settings = {**snapshot_settings, "image_format": "original"}
        if isinstance(summary_settings, dict) and "image_format" not in summary_settings:
            summary_settings = {**summary_settings, "image_format": "original"}
        if (
            not isinstance(summary, dict)
            or summary.get("schema") != "export.run.summary.v3"
            or summary_settings != settings
            or not isinstance(eligibility_digest, str)
            or summary.get("eligibility_digest") != eligibility_digest
            or not isinstance(summary.get("eligibility_evidence"), dict)
            or snapshot_settings != settings
            or not isinstance(entries, list)
        ):
            raise ExportRunError("export_input_changed", "Export input snapshot is incomplete")
        source_root = Path(task.source_root).resolve(strict=False)
        files: list[PlannedFile] = []
        for item in entries:
            if not isinstance(item, dict):
                raise ExportRunError(
                    "export_input_changed", "Export input snapshot file is invalid"
                )
            destination = item.get("destination_relative")
            digest = item.get("sha256")
            size = item.get("size_bytes")
            kind = item.get("kind")
            source_ref = item.get("source_ref")
            content_raw = item.get("content_base64")
            transcode_format = item.get("transcode_format")
            if (
                not isinstance(destination, str)
                or Path(destination).is_absolute()
                or ".." in Path(destination).parts
                or not isinstance(digest, str)
                or len(digest) != 64
                or not all(character in "0123456789abcdef" for character in digest)
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
                or not isinstance(kind, str)
                or (source_ref is None) == (content_raw is None)
                or transcode_format not in {None, "jpeg", "png", "webp"}
                or (transcode_format is not None and source_ref is None)
            ):
                raise ExportRunError(
                    "export_input_changed", "Export input snapshot file is invalid"
                )
            source_path: Path | None = None
            content: bytes | None = None
            if source_ref is not None:
                source_path = self._resolve_snapshot_source(source_ref, source_root=source_root)
            else:
                if not isinstance(content_raw, str):
                    raise ExportRunError(
                        "export_input_changed", "Export generated content is invalid"
                    )
                try:
                    content = b64decode(content_raw.encode("ascii"), validate=True)
                except (ValueError, UnicodeEncodeError) as error:
                    raise ExportRunError(
                        "export_input_changed", "Export generated content is invalid"
                    ) from error
                if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
                    raise ExportRunError(
                        "export_input_changed", "Export generated content identity changed"
                    )
            files.append(
                PlannedFile(
                    destination_relative=destination,
                    sha256=digest,
                    size_bytes=size,
                    kind=kind,
                    source_path=source_path,
                    content=content,
                    transcode_format=transcode_format,
                )
            )
        plan = ExportPlan(
            files=tuple(sorted(files, key=lambda item: item.destination_relative)),
            datasets=(),
            latent_records=(),
            input_digest=input_digest,
        )
        return RunPlan(
            plan=plan,
            summary=summary,
            preview_digest=preview_digest,
            input_snapshot=snapshot,
        )

    def _snapshot_source_ref(self, source_path: Path, *, source_root: Path) -> dict[str, str]:
        resolved = source_path.resolve(strict=False)
        for kind, root in (
            ("task_source", source_root.resolve(strict=False)),
            ("project_cache", (self.project_root or PROJECT_ROOT).resolve(strict=False)),
        ):
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            if not relative.parts:
                break
            return {"kind": kind, "relative_path": PurePosixPath(*relative.parts).as_posix()}
        raise ExportRunError("export_input_changed", "Export source ownership cannot be proven")

    def _resolve_snapshot_source(self, value: object, *, source_root: Path) -> Path:
        if not isinstance(value, dict) or set(value) != {"kind", "relative_path"}:
            raise ExportRunError("export_input_changed", "Export source identity is invalid")
        kind = value.get("kind")
        relative_raw = value.get("relative_path")
        if (
            kind not in {"task_source", "project_cache"}
            or not isinstance(relative_raw, str)
            or not relative_raw
            or "\\" in relative_raw
        ):
            raise ExportRunError("export_input_changed", "Export source identity is invalid")
        relative = PurePosixPath(relative_raw)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ExportRunError("export_input_changed", "Export source identity is invalid")
        root = (
            source_root.resolve(strict=False)
            if kind == "task_source"
            else (self.project_root or PROJECT_ROOT).resolve(strict=False)
        )
        source_path = root.joinpath(*relative.parts).resolve(strict=False)
        try:
            source_path.relative_to(root)
        except ValueError:
            raise ExportRunError(
                "export_input_changed", "Export source ownership cannot be proven"
            ) from None
        return source_path

    @staticmethod
    def _has_builtin_profile(config: object) -> bool:
        if not isinstance(config, dict):
            return False
        return isinstance(config.get("profile"), str) and isinstance(config.get("components"), dict)

    @staticmethod
    def _source_folder(relative_path: str, source_root_name: str) -> tuple[str, tuple[str, ...]]:
        normalized = relative_path.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ExportRunError("export_input_changed", "Sample relative path is unsafe")
        for part in path.parts:
            ExportRunPlanner._safe_folder_name(part)
        if len(path.parts) == 1:
            return ExportRunPlanner._safe_folder_name(source_root_name), path.parts
        return ExportRunPlanner._safe_folder_name(path.parts[0]), path.parts[1:]

    @staticmethod
    def _safe_folder_name(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value.strip() != value
            or value in {".", ".."}
        ):
            raise ExportRunError("export_collision", "Folder name is unsafe")
        if any(
            character in value for character in '<>:"/\\|?*'
        ) or any(ord(character) < 32 for character in value) or value.endswith((".", " ")):
            raise ExportRunError("export_collision", "Folder name is unsafe")
        reserved = {"CON", "PRN", "AUX", "NUL"}
        upper = value.split(".", 1)[0].upper()
        if upper in reserved or re.fullmatch(r"(?:COM|LPT)[1-9]", upper):
            raise ExportRunError("export_collision", "Folder name is reserved")
        return value

    @staticmethod
    def _folder_key(value: str) -> str:
        return unicodedata.normalize("NFC", value).casefold()

    @staticmethod
    def _has_repeat_prefix(value: str) -> bool:
        return re.fullmatch(r"[1-9][0-9]*_(.+)", value) is not None

    @staticmethod
    def _parse_repeat(value: str) -> tuple[int, str]:
        match = re.fullmatch(r"([1-9][0-9]*)_(.+)", value)
        if match is None:
            return 1, value
        return int(match.group(1)), match.group(2)

    @staticmethod
    def _closest_repeat(target: int, image_count: int, original_repeat: int) -> int:
        if image_count <= 0:
            return original_repeat
        candidate = max(1, int(math.floor(target / image_count + 0.5)))
        while candidate > 1 and candidate * image_count > target:
            candidate -= 1
        return max(original_repeat, candidate)

    @staticmethod
    def _dedupe_files(files: list[PlannedFile]) -> list[PlannedFile]:
        by_path: dict[str, PlannedFile] = {}
        for file in files:
            key = ExportRunPlanner._folder_key(file.destination_relative)
            previous = by_path.get(key)
            if previous is not None:
                if (previous.destination_relative, previous.sha256, previous.size_bytes) != (
                    file.destination_relative,
                    file.sha256,
                    file.size_bytes,
                ):
                    raise ExportRunError(
                        "export_collision", f"Export files collide at {file.destination_relative}"
                    )
                continue
            by_path[key] = file
        return sorted(by_path.values(), key=lambda item: item.destination_relative)

    @classmethod
    def _converted_destination(
        cls,
        relative: PurePosixPath,
        *,
        source_relative: PurePosixPath,
        image_format: ImageExportFormat,
        used_paths: set[str],
        sample_id: str,
    ) -> PurePosixPath:
        base = relative.with_suffix(output_suffix(image_format))
        if cls._folder_key(base.as_posix()) not in used_paths:
            used_paths.add(cls._folder_key(base.as_posix()))
            return base
        original_suffix = source_relative.suffix or f"__{sample_id}"
        candidate = base.with_name(f"{base.stem}{original_suffix}{base.suffix}")
        counter = 2
        while cls._folder_key(candidate.as_posix()) in used_paths:
            candidate = base.with_name(f"{base.stem}{original_suffix}_{counter}{base.suffix}")
            counter += 1
        used_paths.add(cls._folder_key(candidate.as_posix()))
        return candidate

    @staticmethod
    def _aesthetic_identity(config: dict[str, Any]) -> dict[str, str]:
        scoring = ScoringConfig.from_task_config(config)
        return {
            "source": EVIDENCE_SOURCES["aesthetic"],
            "model_id": scoring.aesthetic.model_id,
            "config_hash": scoring.inference_config_hash("aesthetic"),
            "algorithm_version": PREPROCESSING_VERSIONS["aesthetic"],
        }

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        return {reason: 0 for reason in _SUMMARY_REASONS}

    @staticmethod
    def _membership_reason(
        sample_id: str,
        *,
        manually_excluded: set[str],
        threshold: float | None,
        evidence_by_sample: dict[str, list[AestheticScoreRecord]],
        identity: dict[str, Any] | None,
    ) -> str:
        if sample_id in manually_excluded:
            return "manual_exclude"
        if threshold is None:
            return "included"
        score, reason = resolve_aesthetic_score(
            evidence_by_sample.get(sample_id, ()), identity=identity
        )
        if reason is not None:
            return reason
        if score is None or score < threshold:
            return "aesthetic_below_minimum"
        return "included"

    @staticmethod
    def _aesthetic_evidence(
        session,
        task_id: str,
        sample_ids: tuple[str, ...],
        *,
        threshold: float | None,
    ) -> dict[str, list[AestheticScoreRecord]]:
        if threshold is None:
            return {}
        rows = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task_id,
                Evidence.sample_id.in_(sample_ids),
                Evidence.code == "aesthetic_score",
            )
            .order_by(Evidence.sample_id, Evidence.id)
        ).all()
        evidence: dict[str, list[AestheticScoreRecord]] = defaultdict(list)
        for row in rows:
            metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
            value = row.value_json
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                value = row.value_number
            evidence[row.sample_id].append(
                AestheticScoreRecord(
                    value=value,
                    source=row.source,
                    model_id=metadata.get("model_id"),
                    config_hash=metadata.get("config_hash"),
                    algorithm_version=row.algorithm_version,
                )
            )
        return evidence

    def _sample(self, task: Task, row: Sample) -> DatasetSample:
        source_root = Path(task.source_root).resolve(strict=True)
        source_path = source_root.joinpath(*Path(row.relative_path).parts).resolve(strict=True)
        source_path.relative_to(source_root)
        if row.extracted_frame_path is None:
            image_path = source_path
        else:
            if self.project_root is None:
                raise ExportRunError(
                    "export_input_changed", "Export run requires a configured project root"
                )
            image_path = self.project_root.joinpath(*Path(row.extracted_frame_path).parts).resolve(
                strict=True
            )
            image_path.relative_to(self.project_root)
        return DatasetSample(
            sample_id=row.id,
            relative_path=row.relative_path,
            artist_scope=row.artist_scope,
            source_path=source_path,
            image_path=image_path,
            source_size=row.source_size,
            source_mtime_ns=row.source_mtime_ns,
            source_sha256=row.source_sha256,
            pixel_sha256=row.pixel_sha256 or "",
            export_requires_render=row.export_requires_render,
        )
