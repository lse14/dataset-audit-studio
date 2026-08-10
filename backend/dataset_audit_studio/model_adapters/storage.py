from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from dataset_audit_studio.model_adapters.errors import (
    ModelIntegrityError,
    ModelRegistryError,
    ModelSchemaError,
)
from dataset_audit_studio.model_adapters.registry import DEFAULT_REGISTRY, ModelRegistry
from dataset_audit_studio.model_adapters.types import (
    FileStatus,
    InstallationManifest,
    InstalledFile,
    ModelSpec,
    ModelStatus,
    OperationSnapshot,
    RegistryFile,
)
from dataset_audit_studio.model_adapters.validation import (
    sha256_file,
    validate_expected_file,
    validate_lse14_replacement,
)
from dataset_audit_studio.runtime import runtime_paths

INSTALLATION_MANIFEST = ".installation.json"


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class ModelStorage:
    def __init__(
        self,
        registry: ModelRegistry = DEFAULT_REGISTRY,
        *,
        models_root: Path | None = None,
    ) -> None:
        self.registry = registry
        self.models_root = (models_root or runtime_paths().models).resolve(strict=False)
        self.registry_root = self.models_root / "registry"
        self.custom_root = self.models_root / "custom"
        self.staging_root = self.models_root / ".staging"
        self.quarantine_root = self.models_root / ".quarantine"
        self._lock = threading.RLock()
        for directory in (
            self.models_root,
            self.registry_root,
            self.custom_root,
            self.staging_root,
            self.quarantine_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
            self._require_under_models(directory)

    def preset_root(self, model: ModelSpec) -> Path:
        root = self.registry_root / model.id / self.registry.version_key(model)
        self._require_under_models(root)
        return root

    def final_path(self, model: ModelSpec, file: RegistryFile) -> Path:
        return self._safe_relative(self.preset_root(model), file.path)

    def part_path(self, model: ModelSpec, file: RegistryFile) -> Path:
        final = self.final_path(model, file)
        return final.with_name(f"{final.name}.part")

    def status(
        self,
        model: ModelSpec,
        operation: OperationSnapshot | None = None,
    ) -> ModelStatus:
        root = self.preset_root(model)
        manifest = self._read_manifest(root / INSTALLATION_MANIFEST)
        installed_by_path = (
            {file.path: file for file in manifest.files}
            if manifest is not None
            and manifest.source_type == "registry"
            and manifest.model_id == model.id
            and manifest.registry_digest == self.registry.digest
            else {}
        )
        file_statuses = tuple(
            self._registry_file_status(model, file, installed_by_path.get(file.path))
            for file in model.files
        )
        derived = self._derived_status(file_statuses)
        verified_at = manifest.verified_at if derived == "ready" and manifest is not None else None
        bytes_downloaded = sum(min(file.present_bytes, file.size) for file in file_statuses)
        bytes_verified = model.total_size if derived == "ready" else 0
        current_file = None
        error = None
        if operation is not None and operation.status != "ready":
            derived = operation.status
            bytes_downloaded = operation.bytes_downloaded
            bytes_verified = operation.bytes_verified
            current_file = operation.current_file
            error = operation.error

        return ModelStatus(
            id=model.id,
            display_name=model.display_name,
            purpose=model.purpose,
            source_kind=model.source.kind,
            repository=model.source.repository,
            revision=model.source.revision,
            homepage=model.source.homepage,
            license=model.source.license,
            loader=model.loader,
            dependencies=model.dependencies,
            replaceable=model.replaceable,
            replacement_schema=model.replacement_schema,
            remote_code_allowed=False,
            total_bytes=model.total_size,
            local_root=self._local_label(root),
            installation_status=derived,
            runtime_ready=False,
            blocking_dependencies=model.dependencies,
            bytes_downloaded=bytes_downloaded,
            bytes_verified=bytes_verified,
            current_file=current_file,
            error=error,
            verified_at=verified_at,
            files=file_statuses,
            is_custom=False,
            base_model_id=None,
        )

    def custom_statuses(self) -> tuple[ModelStatus, ...]:
        statuses: list[ModelStatus] = []
        if not self.custom_root.is_dir():
            return ()
        for directory in sorted(self.custom_root.iterdir(), key=lambda path: path.name.casefold()):
            if not directory.is_dir() or self._is_special(directory):
                continue
            manifest = self._read_manifest(directory / INSTALLATION_MANIFEST)
            if (
                manifest is None
                or manifest.source_type != "custom"
                or manifest.base_model_id is None
            ):
                continue
            try:
                base = self.registry.get(manifest.base_model_id)
            except KeyError:
                continue
            statuses.append(self._custom_status(directory, manifest, base))
        return tuple(statuses)

    def verify_registry_model(
        self,
        model: ModelSpec,
        *,
        progress: Callable[[str, int], None] | None = None,
    ) -> InstallationManifest:
        installed: list[InstalledFile] = []
        completed = 0
        root = self.preset_root(model)
        for expected in model.files:
            path = self.final_path(model, expected)
            try:
                self.require_safe_file(path, expected.path)
                before = path.stat()
                validate_expected_file(
                    path,
                    expected,
                    progress=(
                        (
                            lambda current, name=expected.path, base=completed: progress(
                                name, base + current
                            )
                        )
                        if progress is not None
                        else None
                    ),
                )
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise ModelIntegrityError(
                        f"Model file changed during verification: {expected.path}"
                    )
            except (ModelIntegrityError, ModelSchemaError):
                self._invalidate_registry_install(root, path, model.id)
                raise
            installed.append(
                InstalledFile(
                    path=expected.path,
                    size=expected.size,
                    sha256=expected.sha256,
                    mtime_ns=after.st_mtime_ns,
                )
            )
            completed += expected.size
            if progress is not None:
                progress(expected.path, completed)
        return self.write_registry_manifest(model, tuple(installed))

    def write_registry_manifest(
        self,
        model: ModelSpec,
        installed: tuple[InstalledFile, ...],
    ) -> InstallationManifest:
        expected = {file.path: file for file in model.files}
        actual = {file.path: file for file in installed}
        if set(expected) != set(actual):
            raise ModelIntegrityError("Verified model files do not match the registry file set")
        for path, file in actual.items():
            registered = expected[path]
            if file.size != registered.size or file.sha256 != registered.sha256:
                raise ModelIntegrityError(f"Verified metadata does not match registry for {path}")
            current = self.final_path(model, registered).stat()
            if current.st_size != file.size or current.st_mtime_ns != file.mtime_ns:
                raise ModelIntegrityError(f"Model file changed before manifest commit: {path}")
        manifest = InstallationManifest(
            schema_version=1,
            model_id=model.id,
            source_type="registry",
            registry_digest=self.registry.digest,
            registry_version=self.registry.document.registry_version,
            display_name=model.display_name,
            loader=model.loader,
            files=installed,
            verified_at=_utc_iso(),
        )
        self._write_manifest(self.preset_root(model) / INSTALLATION_MANIFEST, manifest)
        return manifest

    def register_local_replacement(
        self,
        *,
        base_model_id: str,
        source_path: Path,
        display_name: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> ModelStatus:
        base = self.registry.get(base_model_id)
        if not base.replaceable or base.replacement_schema is None:
            raise ModelRegistryError(f"Model does not support local replacement: {base_model_id}")
        if not source_path.is_absolute():
            raise ModelRegistryError("Local replacement path must be absolute")
        if source_path.suffix.casefold() != ".safetensors":
            raise ModelRegistryError("Local replacement must be a .safetensors file")
        if source_path.is_symlink():
            raise ModelRegistryError("Local replacement cannot be a symbolic link")
        try:
            source_lstat = source_path.lstat()
        except OSError as error:
            raise ModelRegistryError(f"Local replacement is not readable: {error}") from error
        if _is_reparse(source_lstat) or not stat.S_ISREG(source_lstat.st_mode):
            raise ModelRegistryError("Local replacement must be a regular non-reparse file")
        source = source_path.resolve(strict=True)
        before = source.stat()
        if before.st_size <= 8:
            raise ModelSchemaError("Local replacement is too small to be safetensors")
        name = (display_name or "").strip()
        if display_name is not None and (not name or len(name) > 160):
            raise ModelRegistryError("Local replacement display name must be 1-160 characters")

        staging = self.staging_root / f"local-{uuid4().hex}.safetensors.part"
        self._require_under_models(staging)
        digest = hashlib.sha256()
        copied = 0
        try:
            with source.open("rb") as input_stream, staging.open("xb") as output_stream:
                while chunk := input_stream.read(4 * 1024 * 1024):
                    output_stream.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                    if progress is not None:
                        progress(copied, before.st_size)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            after = source.stat()
            if (
                copied != before.st_size
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise ModelIntegrityError("Local replacement changed while it was being copied")

            tensor_summary = validate_lse14_replacement(staging)
            sha256 = digest.hexdigest()
            custom_id = f"custom_{base.id}_{sha256[:12]}"
            custom_dir = self.custom_root / custom_id
            self._require_under_models(custom_dir)
            custom_dir.mkdir(parents=True, exist_ok=True)
            final = custom_dir / "model.safetensors"
            with self._lock:
                if final.exists():
                    if self._is_special(final) or sha256_file(final) != sha256:
                        self.quarantine(final, model_id=custom_id)
                    else:
                        staging.unlink(missing_ok=True)
                if staging.exists():
                    os.replace(staging, final)
                final_stat = final.stat()
                name = name or f"{base.display_name} (local {sha256[:8]})"
                manifest = InstallationManifest(
                    schema_version=1,
                    model_id=custom_id,
                    source_type="custom",
                    registry_digest=self.registry.digest,
                    registry_version=self.registry.document.registry_version,
                    base_model_id=base.id,
                    display_name=name,
                    loader=base.loader,
                    replacement_schema=base.replacement_schema,
                    original_filename=source.name,
                    source_path=str(source),
                    files=(
                        InstalledFile(
                            path="model.safetensors",
                            size=final_stat.st_size,
                            sha256=sha256,
                            mtime_ns=final_stat.st_mtime_ns,
                        ),
                    ),
                    tensor_summary=tensor_summary,
                    verified_at=_utc_iso(),
                )
                self._write_manifest(custom_dir / INSTALLATION_MANIFEST, manifest)
            return self._custom_status(custom_dir, manifest, base)
        finally:
            staging.unlink(missing_ok=True)

    def verify_custom_model(self, model_id: str) -> ModelStatus:
        directory = self.custom_root / model_id
        self._require_under_models(directory)
        manifest = self._read_manifest(directory / INSTALLATION_MANIFEST)
        if manifest is None or manifest.source_type != "custom" or manifest.base_model_id is None:
            raise ModelRegistryError(f"Custom model registration is invalid: {model_id}")
        base = self.registry.get(manifest.base_model_id)
        if manifest.replacement_schema != "lse14_fusion_multitask_v1":
            raise ModelSchemaError("Unsupported custom model replacement schema")
        expected = manifest.files[0]
        path = self._safe_relative(directory, expected.path)
        try:
            self.require_safe_file(path, expected.path)
            before = path.stat()
            digest = sha256_file(path)
            if before.st_size != expected.size or digest != expected.sha256:
                raise ModelIntegrityError(
                    "Custom model size or SHA-256 no longer matches registration"
                )
            summary = validate_lse14_replacement(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ModelIntegrityError("Custom model changed during verification")
        except (ModelIntegrityError, ModelSchemaError):
            with suppress(ModelRegistryError):
                self.quarantine(path, model_id=model_id)
            raise
        updated = manifest.model_copy(
            update={
                "files": (
                    InstalledFile(
                        path=expected.path,
                        size=after.st_size,
                        sha256=digest,
                        mtime_ns=after.st_mtime_ns,
                    ),
                ),
                "tensor_summary": summary,
                "verified_at": _utc_iso(),
            }
        )
        self._write_manifest(directory / INSTALLATION_MANIFEST, updated)
        return self._custom_status(directory, updated, base)

    def quarantine(self, path: Path, *, model_id: str) -> Path:
        self._require_under_models(path)
        if not path.exists() and not path.is_symlink():
            return path
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = self.quarantine_root / (
            f"{model_id}-{stamp}-{uuid4().hex[:8]}-{path.name}"
        )
        self._require_under_models(destination)
        os.replace(path, destination)
        return destination

    def _invalidate_registry_install(self, root: Path, path: Path, model_id: str) -> None:
        with suppress(ModelRegistryError):
            self.quarantine(path, model_id=model_id)
        manifest = root / INSTALLATION_MANIFEST
        if manifest.exists() or manifest.is_symlink():
            with suppress(ModelRegistryError):
                self.quarantine(manifest, model_id=model_id)

    def _registry_file_status(
        self,
        model: ModelSpec,
        expected: RegistryFile,
        installed: InstalledFile | None,
    ) -> FileStatus:
        final = self.final_path(model, expected)
        part = self.part_path(model, expected)
        present = 0
        state = "missing"
        if final.exists():
            if self._is_special(final) or not final.is_file():
                state = "corrupt"
            else:
                current = final.stat()
                present = current.st_size
                if current.st_size != expected.size:
                    state = "corrupt"
                elif (
                    installed is not None
                    and installed.size == expected.size
                    and installed.sha256 == expected.sha256
                    and installed.mtime_ns == current.st_mtime_ns
                ):
                    state = "ready"
                else:
                    state = "verification_required"
        elif part.exists():
            if self._is_special(part) or not part.is_file():
                state = "corrupt"
            else:
                present = part.stat().st_size
                state = "partial" if 0 < present <= expected.size else "corrupt"
        return FileStatus(
            path=expected.path,
            size=expected.size,
            sha256=expected.sha256,
            present_bytes=present,
            state=state,
        )

    def _custom_status(
        self,
        directory: Path,
        manifest: InstallationManifest,
        base: ModelSpec,
    ) -> ModelStatus:
        statuses: list[FileStatus] = []
        for expected in manifest.files:
            path = self._safe_relative(directory, expected.path)
            if not path.exists():
                state = "missing"
                present = 0
            elif self._is_special(path) or not path.is_file():
                state = "corrupt"
                present = 0
            else:
                current = path.stat()
                present = current.st_size
                if current.st_size != expected.size:
                    state = "corrupt"
                elif current.st_mtime_ns != expected.mtime_ns:
                    state = "verification_required"
                else:
                    state = "ready"
            statuses.append(
                FileStatus(
                    path=expected.path,
                    size=expected.size,
                    sha256=expected.sha256,
                    present_bytes=present,
                    state=state,
                )
            )
        derived = self._derived_status(tuple(statuses))
        total = sum(file.size for file in manifest.files)
        return ModelStatus(
            id=manifest.model_id,
            display_name=manifest.display_name,
            purpose=base.purpose,
            source_kind="local",
            repository=None,
            revision=manifest.files[0].sha256,
            homepage=base.source.homepage,
            license="user-provided",
            loader=manifest.loader,
            dependencies=base.dependencies,
            replaceable=False,
            replacement_schema=manifest.replacement_schema,
            remote_code_allowed=False,
            total_bytes=total,
            local_root=self._local_label(directory),
            installation_status=derived,
            runtime_ready=False,
            blocking_dependencies=base.dependencies,
            bytes_downloaded=sum(min(file.present_bytes, file.size) for file in statuses),
            bytes_verified=total if derived == "ready" else 0,
            current_file=None,
            error=None,
            verified_at=manifest.verified_at if derived == "ready" else None,
            files=tuple(statuses),
            is_custom=True,
            base_model_id=base.id,
        )

    @staticmethod
    def _derived_status(files: tuple[FileStatus, ...]) -> str:
        states = {file.state for file in files}
        if states == {"ready"}:
            return "ready"
        if "corrupt" in states:
            return "corrupt"
        if "verification_required" in states:
            return "verification_required"
        if "partial" in states:
            return "partial"
        return "missing"

    def _write_manifest(self, path: Path, manifest: InstallationManifest) -> None:
        self._require_under_models(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        part = path.with_name(f"{path.name}.{uuid4().hex}.part")
        try:
            with part.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(part, path)
        finally:
            part.unlink(missing_ok=True)

    def _read_manifest(self, path: Path) -> InstallationManifest | None:
        try:
            if self._is_special(path):
                return None
            return InstallationManifest.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            return None

    def _safe_relative(self, root: Path, relative: str) -> Path:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
            raise ModelRegistryError(f"Unsafe model path: {relative}")
        root_resolved = root.resolve(strict=False)
        candidate = root.joinpath(*pure.parts).resolve(strict=False)
        try:
            candidate.relative_to(root_resolved)
        except ValueError as error:
            raise ModelRegistryError(
                f"Model path escapes its installation root: {relative}"
            ) from error
        self._require_under_models(candidate)
        return candidate

    def _require_under_models(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self.models_root)
        except ValueError as error:
            raise ModelRegistryError(f"Model path escapes project model root: {path}") from error

    def require_safe_file(self, path: Path, label: str) -> None:
        if not path.exists() or self._is_special(path) or not path.is_file():
            raise ModelIntegrityError(f"Model file is missing or unsafe: {label}")

    @staticmethod
    def _is_special(path: Path) -> bool:
        try:
            result = path.lstat()
        except OSError:
            return False
        return path.is_symlink() or _is_reparse(result)

    def _local_label(self, path: Path) -> str:
        relative = path.resolve(strict=False).relative_to(self.models_root)
        return (PurePosixPath("models") / PurePosixPath(relative.as_posix())).as_posix()


def manifest_to_dict(manifest: InstallationManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json")
