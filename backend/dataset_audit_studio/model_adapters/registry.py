from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from dataset_audit_studio.model_adapters.errors import ModelRegistryError
from dataset_audit_studio.model_adapters.types import ModelSpec, RegistryDocument, RegistryFile

REGISTRY_PATH = Path(__file__).with_name("registry.json")


class ModelNotFound(ModelRegistryError):
    pass


class ModelRegistry:
    def __init__(self, document: RegistryDocument, *, digest: str, source_path: Path) -> None:
        self.document = document
        self.digest = digest
        self.source_path = source_path
        self._models = {model.id: model for model in document.models}

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> ModelRegistry:
        data = path.read_bytes()
        document = RegistryDocument.model_validate_json(data)
        canonical = json.dumps(
            document.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            document,
            digest=hashlib.sha256(canonical).hexdigest(),
            source_path=path.resolve(strict=True),
        )

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self._models[model_id]
        except KeyError as error:
            raise ModelNotFound(f"Unknown model id: {model_id}") from error

    def all(self) -> tuple[ModelSpec, ...]:
        return self.document.models

    def dependency_order(self, model_id: str) -> tuple[ModelSpec, ...]:
        ordered: list[ModelSpec] = []
        visited: set[str] = set()

        def visit(current_id: str) -> None:
            if current_id in visited:
                return
            current = self.get(current_id)
            for dependency in current.dependencies:
                visit(dependency)
            visited.add(current_id)
            ordered.append(current)

        visit(model_id)
        return tuple(ordered)

    def file_url(self, model: ModelSpec, file: RegistryFile) -> str:
        if model.source.kind == "huggingface":
            assert model.source.repository is not None
            assert model.source.revision is not None
            encoded_path = quote(file.path, safe="/")
            return (
                f"https://huggingface.co/{model.source.repository}/resolve/"
                f"{model.source.revision}/{encoded_path}"
            )
        assert file.url is not None
        return file.url

    @staticmethod
    def version_key(model: ModelSpec) -> str:
        return model.source.revision or model.files[0].sha256[:40]


DEFAULT_REGISTRY = ModelRegistry.load()
