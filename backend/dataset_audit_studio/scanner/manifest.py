from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from dataset_audit_studio.runtime import PROJECT_ROOT
from dataset_audit_studio.scanner.config import ScanConfig
from dataset_audit_studio.scanner.discovery import discover_media
from dataset_audit_studio.scanner.types import (
    DiscoveredMedia,
    DiscoveryResult,
    ManifestInfo,
)

MANIFEST_SCHEMA_VERSION = 1


def manifest_path(
    task_id: str,
    config_hash: str,
    *,
    project_root: Path | None = None,
) -> Path:
    return (
        (project_root or PROJECT_ROOT).resolve(strict=False)
        / "data"
        / "tasks"
        / task_id
        / "manifests"
        / f"scan-{config_hash}.jsonl"
    )


def _json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    task_id: str,
    source_root: Path,
    config_hash: str,
    config: ScanConfig,
    *,
    project_root: Path | None = None,
) -> tuple[ManifestInfo, DiscoveryResult]:
    source_root = source_root.resolve(strict=True)
    project_root = (project_root or PROJECT_ROOT).resolve(strict=False)
    discovery = discover_media(source_root, config, project_root=project_root)
    destination = manifest_path(task_id, config_hash, project_root=project_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    header = {
        "record_type": "header",
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_root": str(source_root),
        "config_hash": config_hash,
        "scan_config": config.cache_payload(),
        "created_at": datetime.now(UTC).isoformat(),
        "item_count": len(discovery.items),
        "ignored_reparse_count": discovery.ignored_reparse_count,
        "ignored_directory_count": discovery.ignored_directory_count,
    }
    with part.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(_json_line(header) + "\n")
        for item in discovery.items:
            stream.write(
                _json_line(
                    {
                        "record_type": "media",
                        "relative_path": item.relative_path,
                        "source_size": item.source_size,
                        "source_mtime_ns": item.source_mtime_ns,
                        "media_kind_hint": item.media_kind_hint,
                        "artist_scope": item.artist_scope,
                    }
                )
                + "\n"
            )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(part, destination)
    info = ManifestInfo(
        path=destination,
        sha256=_file_sha256(destination),
        item_count=len(discovery.items),
        ignored_reparse_count=discovery.ignored_reparse_count,
        ignored_directory_count=discovery.ignored_directory_count,
    )
    return info, discovery


def _safe_source_path(source_root: Path, relative_path: str) -> Path:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"Unsafe path in scan manifest: {relative_path}")
    candidate = source_root.joinpath(*pure.parts).resolve(strict=False)
    candidate.relative_to(source_root)
    return candidate


def load_manifest(
    path: Path,
    *,
    source_root: Path,
    expected_config_hash: str,
    expected_sha256: str | None = None,
    project_root: Path | None = None,
) -> tuple[ManifestInfo, DiscoveryResult]:
    project_root = (project_root or PROJECT_ROOT).resolve(strict=False)
    path = path.resolve(strict=True)
    path.relative_to(project_root)
    actual_sha256 = _file_sha256(path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ValueError("Scan manifest SHA-256 does not match checkpoint")

    source_root = source_root.resolve(strict=True)
    with path.open("r", encoding="utf-8") as stream:
        try:
            header = json.loads(next(stream))
        except (StopIteration, json.JSONDecodeError) as error:
            raise ValueError("Scan manifest has no valid header") from error
        if header.get("record_type") != "header":
            raise ValueError("Scan manifest first record is not a header")
        if header.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("Unsupported scan manifest schema")
        if header.get("config_hash") != expected_config_hash:
            raise ValueError("Scan manifest config hash does not match task")
        if Path(header.get("source_root", "")).resolve(strict=False) != source_root:
            raise ValueError("Scan manifest source root does not match task")

        items: list[DiscoveredMedia] = []
        for line_number, line in enumerate(stream, start=2):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid manifest JSON at line {line_number}") from error
            if record.get("record_type") != "media":
                raise ValueError(f"Unexpected manifest record at line {line_number}")
            relative_path = str(record["relative_path"])
            absolute_path = _safe_source_path(source_root, relative_path)
            items.append(
                DiscoveredMedia(
                    absolute_path=absolute_path,
                    relative_path=relative_path,
                    source_size=int(record["source_size"]),
                    source_mtime_ns=int(record["source_mtime_ns"]),
                    media_kind_hint=str(record["media_kind_hint"]),
                    artist_scope=str(record["artist_scope"]),
                )
            )

    if len(items) != int(header.get("item_count", -1)):
        raise ValueError("Scan manifest item count does not match header")
    discovery = DiscoveryResult(
        items=tuple(items),
        ignored_reparse_count=int(header.get("ignored_reparse_count", 0)),
        ignored_directory_count=int(header.get("ignored_directory_count", 0)),
    )
    info = ManifestInfo(
        path=path,
        sha256=actual_sha256,
        item_count=len(items),
        ignored_reparse_count=discovery.ignored_reparse_count,
        ignored_directory_count=discovery.ignored_directory_count,
    )
    return info, discovery
