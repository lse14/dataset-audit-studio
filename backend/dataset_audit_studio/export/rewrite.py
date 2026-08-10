from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

BACKUP_DIRECTORY_SUFFIX = ".dataset-audit-studio-backups"


def backup_root(source_root: Path, task_id: str) -> Path:
    root = source_root.resolve(strict=True)
    return root.parent / f".{root.name}{BACKUP_DIRECTORY_SUFFIX}" / task_id


def rewrite_preview_digest(
    *,
    task_id: str,
    config_hash: str,
    config_revision: int,
    curated_sample_ids: tuple[str, ...],
    paths: tuple[Path, ...],
    source_root: Path,
) -> str:
    root = source_root.resolve(strict=True)
    entries = [
        [path.relative_to(root).as_posix(), _sha256(path), path.stat().st_size]
        for path in _validated_paths(root, paths)
    ]
    payload = {
        "task_id": task_id,
        "config_hash": config_hash,
        "config_revision": config_revision,
        "curated_sample_ids": sorted(curated_sample_ids),
        "files": entries,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def execute_rewrite(
    source_root: Path,
    task_id: str,
    paths: tuple[Path, ...],
    *,
    backup_enabled: bool,
) -> dict[str, object]:
    if backup_enabled is not True:
        raise ValueError("backup_enabled must be true for rewrite")
    root = source_root.resolve(strict=True)
    files = _validated_paths(root, paths)
    if not files:
        return {"deleted_files": 0, "backup_path": None}

    created = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_root(root, task_id) / created
    destination.mkdir(parents=True, exist_ok=False)
    manifest = destination / "manifest.json"
    entries = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        entries.append(
            {
                "relative_path": relative,
                "sha256": _sha256(path),
                "moved": False,
            }
        )
    payload: dict[str, object] = {
        "version": 1,
        "task_id": task_id,
        "source_root": str(root),
        "created_at": created,
        "state": "active",
        "files": entries,
    }
    _write_manifest(manifest, payload)
    moved: list[tuple[dict[str, object], Path, Path]] = []
    try:
        for entry, source in zip(entries, files, strict=True):
            target = destination.joinpath(*Path(str(entry["relative_path"])).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            entry["moved"] = True
            moved.append((entry, source, target))
            _write_manifest(manifest, payload)
    except Exception:
        # A failed publish must leave the source tree exactly as it was. The
        # manifest is useful during a successful rewrite, but partial backups
        # are discarded after the already-moved files are restored.
        for entry, source, target in reversed(moved):
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(source))
            entry["moved"] = False
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {"deleted_files": len(files), "backup_path": str(destination)}


def restore_latest_backup(source_root: Path, task_id: str) -> dict[str, object]:
    root = source_root.resolve(strict=True)
    candidates = sorted(backup_root(root, task_id).glob("*/manifest.json"), reverse=True)
    for manifest in candidates:
        if _is_active_backup(manifest, root):
            return _restore_backup_manifest(root, manifest)
    raise FileNotFoundError("No active rewrite backup exists for this task")


def restore_backup(
    source_root: Path,
    task_id: str,
    backup_path: Path,
) -> dict[str, object]:
    root = source_root.resolve(strict=True)
    destination = backup_path.resolve(strict=True)
    destination.relative_to(backup_root(root, task_id).resolve(strict=False))
    manifest = destination / "manifest.json"
    if not manifest.is_file() or not _is_active_backup(manifest, root):
        raise ValueError("Rewrite backup is not active for this source root")
    return _restore_backup_manifest(root, manifest)


def _validated_paths(source_root: Path, paths: tuple[Path, ...]) -> tuple[Path, ...]:
    result: dict[str, Path] = {}
    for path in paths:
        resolved = path.resolve(strict=True)
        resolved.relative_to(source_root)
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError(f"Rewrite target must be a regular source file: {resolved}")
        result[str(resolved).casefold()] = resolved
    return tuple(result[key] for key in sorted(result))


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError("Rewrite backup has an unsafe relative path")
    return path


def _is_active_backup(manifest: Path, source_root: Path) -> bool:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    return payload.get("state") == "active" and payload.get("source_root") == str(source_root)


def _restore_backup_manifest(source_root: Path, manifest: Path) -> dict[str, object]:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise ValueError("Rewrite backup manifest is invalid")
    planned: list[tuple[dict[str, object], Path, Path]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("moved") is not True:
            continue
        relative = _safe_relative(str(entry.get("relative_path", "")))
        source = manifest.parent.joinpath(*relative.parts)
        target = source_root.joinpath(*relative.parts)
        if target.exists():
            raise FileExistsError(f"Cannot restore over existing source file: {target}")
        if not source.is_file() or _sha256(source) != entry.get("sha256"):
            raise RuntimeError(
                f"Rewrite backup file is missing or changed: {relative.as_posix()}"
            )
        planned.append((entry, source, target))

    restored: list[tuple[dict[str, object], Path, Path]] = []
    try:
        for entry, source, target in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            restored.append((entry, source, target))
    except Exception:
        for _entry, source, target in reversed(restored):
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(source))
        raise
    payload["state"] = "restored"
    _write_manifest(manifest, payload)
    return {"restored_files": len(restored), "backup_path": str(manifest.parent)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
