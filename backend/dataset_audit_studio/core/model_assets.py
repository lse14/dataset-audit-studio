from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AssetFile:
    path: str
    size: int
    sha256: str
    mtime_ns: int


@dataclass(frozen=True)
class ModelAsset:
    model_id: str
    loader: str
    root: str
    files: tuple[AssetFile, ...]
    dependencies: tuple[str, ...]
    is_custom: bool
    base_model_id: str | None

    def file_path(self, relative_path: str) -> Path:
        match = next((item for item in self.files if item.path == relative_path), None)
        if match is None:
            raise KeyError(f"Model {self.model_id} has no registered file {relative_path}")
        root = Path(self.root).resolve(strict=True)
        path = root.joinpath(*Path(relative_path).parts).resolve(strict=True)
        path.relative_to(root)
        return path


@dataclass(frozen=True)
class RuntimeAssets:
    models_root: str
    models: tuple[ModelAsset, ...]

    def get(self, model_id: str) -> ModelAsset:
        try:
            return next(model for model in self.models if model.model_id == model_id)
        except StopIteration as error:
            raise KeyError(f"Runtime asset is missing: {model_id}") from error


def select_runtime_assets(
    assets: RuntimeAssets,
    model_ids: tuple[str, ...],
) -> RuntimeAssets:
    if len(model_ids) != len(set(model_ids)):
        raise ValueError("Runtime asset selection contains duplicate model ids")
    selected = tuple(assets.get(model_id) for model_id in model_ids)
    return RuntimeAssets(models_root=assets.models_root, models=selected)


def verify_runtime_asset_snapshot(assets: RuntimeAssets) -> None:
    models_root = Path(assets.models_root).resolve(strict=True)
    for model in assets.models:
        root = Path(model.root).resolve(strict=True)
        root.relative_to(models_root)
        for expected in model.files:
            path = root.joinpath(*Path(expected.path).parts).resolve(strict=True)
            path.relative_to(root)
            stat = path.stat()
            if stat.st_size != expected.size or stat.st_mtime_ns != expected.mtime_ns:
                raise RuntimeError(
                    "Verified model asset changed before inference: "
                    f"{model.model_id}/{expected.path}"
                )


def runtime_model_digest(
    assets: RuntimeAssets,
    model_ids: tuple[str, ...],
) -> str:
    digest = hashlib.sha256()
    for model_id in sorted(model_ids):
        model = assets.get(model_id)
        for file in sorted(model.files, key=lambda item: item.path):
            digest.update(f"{model_id}\0{file.path}\0{file.sha256}\n".encode())
    return digest.hexdigest()
