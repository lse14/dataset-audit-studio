from __future__ import annotations

import threading
from pathlib import Path

from dataset_audit_studio.model_adapters.downloads import ModelDownloadManager
from dataset_audit_studio.model_adapters.errors import ModelRegistryError
from dataset_audit_studio.model_adapters.registry import (
    DEFAULT_REGISTRY,
    ModelNotFound,
    ModelRegistry,
)
from dataset_audit_studio.model_adapters.storage import ModelStorage
from dataset_audit_studio.model_adapters.types import ModelStatus, OperationSnapshot


class ModelService:
    def __init__(
        self,
        storage: ModelStorage,
        downloads: ModelDownloadManager,
        registry: ModelRegistry = DEFAULT_REGISTRY,
    ) -> None:
        self.storage = storage
        self.downloads = downloads
        self.registry = registry
        self._runtime_lock = threading.RLock()
        self._runtime_verified: set[str] = set()

    def list_models(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        purpose: str | None = None,
        installation_status: str | None = None,
    ) -> tuple[list[ModelStatus], int]:
        operations = self.downloads.snapshots()
        statuses = [
            self.storage.status(model, operations.get(model.id)) for model in self.registry.all()
        ]
        statuses.extend(self.storage.custom_statuses())
        statuses = self._with_dependency_state(statuses)
        if purpose is not None:
            statuses = [model for model in statuses if model.purpose == purpose]
        if installation_status is not None:
            statuses = [
                model for model in statuses if model.installation_status == installation_status
            ]
        statuses.sort(key=lambda model: (model.is_custom, model.purpose, model.id))
        return statuses[offset : offset + limit], len(statuses)

    def get_model(self, model_id: str) -> ModelStatus:
        statuses, _ = self.list_models(limit=1000)
        for model in statuses:
            if model.id == model_id:
                return model
        raise ModelNotFound(f"Unknown model id: {model_id}")

    def download(
        self,
        model_id: str,
        *,
        include_dependencies: bool = True,
    ) -> tuple[OperationSnapshot, ...]:
        return self.downloads.start_download(
            model_id,
            include_dependencies=include_dependencies,
        )

    def download_all(self) -> tuple[OperationSnapshot, ...]:
        return self.downloads.start_download_all()

    def cancel(self, model_id: str) -> OperationSnapshot:
        return self.downloads.cancel(model_id)

    def verify(self, model_id: str) -> tuple[ModelStatus, OperationSnapshot | None]:
        try:
            self.registry.get(model_id)
        except ModelRegistryError:
            self.storage.verify_custom_model(model_id)
            return self.get_model(model_id), None
        operation = self.downloads.start_verify(model_id)
        return self.get_model(model_id), operation

    def register_local(
        self,
        *,
        base_model_id: str,
        source_path: Path,
        display_name: str | None = None,
    ) -> ModelStatus:
        registered = self.storage.register_local_replacement(
            base_model_id=base_model_id,
            source_path=source_path,
            display_name=display_name,
        )
        return self.get_model(registered.id)

    def require_ready(self, model_id: str) -> Path:
        status = self.get_model(model_id)
        if not status.runtime_ready:
            if status.is_custom:
                for dependency in status.blocking_dependencies:
                    self.download(dependency, include_dependencies=True)
            else:
                self.download(model_id, include_dependencies=True)
            raise ModelRegistryError(
                f"Model {model_id} is not runtime-ready; installation status is "
                f"{status.installation_status}, blocking dependencies are "
                f"{list(status.blocking_dependencies)}"
            )
        with self._runtime_lock:
            if model_id not in self._runtime_verified:
                if status.is_custom:
                    verified_dependencies: set[str] = set()
                    for dependency in status.dependencies:
                        for model in self.registry.dependency_order(dependency):
                            if model.id not in verified_dependencies:
                                self.storage.verify_registry_model(model)
                                verified_dependencies.add(model.id)
                                self._runtime_verified.add(model.id)
                    self.storage.verify_custom_model(model_id)
                else:
                    for model in self.registry.dependency_order(model_id):
                        if model.id not in self._runtime_verified:
                            self.storage.verify_registry_model(model)
                            self._runtime_verified.add(model.id)
                self._runtime_verified.add(model_id)
                status = self.get_model(model_id)
        relative = Path(*Path(status.local_root).parts[1:])
        root = (self.storage.models_root / relative).resolve(strict=True)
        root.relative_to(self.storage.models_root)
        return root

    def health(self) -> dict[str, object]:
        statuses, total = self.list_models(limit=1000)
        return {
            "registry_version": self.registry.document.registry_version,
            "registry_digest": self.registry.digest,
            "registered_models": len(self.registry.all()),
            "custom_models": sum(model.is_custom for model in statuses),
            "ready_models": sum(model.installation_status == "ready" for model in statuses),
            "runtime_ready_models": sum(model.runtime_ready for model in statuses),
            "active_operations": self.downloads.active_count(),
            "models_root": str(self.storage.models_root),
            "total_models": total,
            "remote_code_allowed": False,
        }

    def shutdown(self) -> bool:
        return self.downloads.shutdown()

    @staticmethod
    def _with_dependency_state(statuses: list[ModelStatus]) -> list[ModelStatus]:
        by_id = {model.id: model for model in statuses}
        ready_cache: dict[str, bool] = {}

        def is_runtime_ready(model_id: str) -> bool:
            if model_id in ready_cache:
                return ready_cache[model_id]
            model = by_id.get(model_id)
            if model is None or model.installation_status != "ready":
                ready_cache[model_id] = False
                return False
            ready_cache[model_id] = all(
                is_runtime_ready(dependency) for dependency in model.dependencies
            )
            return ready_cache[model_id]

        enriched: list[ModelStatus] = []
        for model in statuses:
            blockers = tuple(
                dependency
                for dependency in model.dependencies
                if not is_runtime_ready(dependency)
            )
            enriched.append(
                model.model_copy(
                    update={
                        "runtime_ready": is_runtime_ready(model.id),
                        "blocking_dependencies": blockers,
                    }
                )
            )
        return enriched
