from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
from safetensors import safe_open
from safetensors.numpy import save_file

from dataset_audit_studio.clustering.types import EmbeddingShard
from dataset_audit_studio.runtime import PROJECT_ROOT


class EmbeddingShardStore:
    def __init__(self, *, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve(strict=False)

    def cache_key(
        self,
        *,
        sample_ids: tuple[str, ...],
        pixel_hashes: tuple[str, ...],
        model_sha256: str,
        preprocessing_version: str,
    ) -> str:
        if len(sample_ids) != len(pixel_hashes):
            raise ValueError("Embedding shard ids and hashes must have equal lengths")
        digest = hashlib.sha256()
        digest.update(f"{model_sha256}\0{preprocessing_version}\n".encode())
        for sample_id, pixel_hash in zip(sample_ids, pixel_hashes, strict=True):
            digest.update(f"{sample_id}\0{pixel_hash}\n".encode())
        return digest.hexdigest()

    def write(
        self,
        *,
        task_id: str,
        sample_ids: tuple[str, ...],
        pixel_hashes: tuple[str, ...],
        embeddings: np.ndarray,
        model_sha256: str,
        preprocessing_version: str,
    ) -> EmbeddingShard:
        matrix = np.asarray(embeddings, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(sample_ids):
            raise ValueError("Embedding matrix shape does not match shard sample ids")
        if np.any(~np.isfinite(matrix)):
            raise ValueError("Embedding matrix contains non-finite values")
        key = self.cache_key(
            sample_ids=sample_ids,
            pixel_hashes=pixel_hashes,
            model_sha256=model_sha256,
            preprocessing_version=preprocessing_version,
        )
        directory = self._directory(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / f"{key}.safetensors"
        part = final.with_suffix(final.suffix + ".part")
        metadata = {
            "schema": "siglip2_embedding_shard_v1",
            "sample_ids_json": json.dumps(sample_ids, separators=(",", ":")),
            "pixel_hashes_json": json.dumps(pixel_hashes, separators=(",", ":")),
            "model_sha256": model_sha256,
            "preprocessing_version": preprocessing_version,
        }
        save_file({"embeddings": matrix.astype(np.float16)}, str(part), metadata=metadata)
        with part.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(part, final)
        return self.inspect(task_id=task_id, cache_key=key)

    def inspect(self, *, task_id: str, cache_key: str) -> EmbeddingShard:
        path = self._directory(task_id) / f"{cache_key}.safetensors"
        resolved = path.resolve(strict=True)
        resolved.relative_to(self.project_root)
        with safe_open(str(resolved), framework="np") as tensors:
            metadata = dict(tensors.metadata() or {})
            if metadata.get("schema") != "siglip2_embedding_shard_v1":
                raise RuntimeError("Embedding shard has an unsupported schema")
            matrix = tensors.get_tensor("embeddings")
        sample_ids = tuple(json.loads(metadata["sample_ids_json"]))
        pixel_hashes = tuple(json.loads(metadata["pixel_hashes_json"]))
        if matrix.ndim != 2 or matrix.shape[0] != len(sample_ids):
            raise RuntimeError("Embedding shard metadata does not match its tensor")
        stat = resolved.stat()
        return EmbeddingShard(
            cache_key=cache_key,
            relative_path=resolved.relative_to(self.project_root).as_posix(),
            sample_ids=sample_ids,
            pixel_hashes=pixel_hashes,
            model_sha256=metadata["model_sha256"],
            preprocessing_version=metadata["preprocessing_version"],
            sha256=self._sha256(resolved),
            size_bytes=stat.st_size,
            rows=matrix.shape[0],
            dimensions=matrix.shape[1],
        )

    def try_inspect(self, *, task_id: str, cache_key: str) -> EmbeddingShard | None:
        try:
            return self.inspect(task_id=task_id, cache_key=cache_key)
        except FileNotFoundError:
            return None

    def load(self, shard: EmbeddingShard) -> np.ndarray:
        path = self.project_root.joinpath(*Path(shard.relative_path).parts).resolve(strict=True)
        path.relative_to(self.project_root)
        if self._sha256(path) != shard.sha256:
            raise RuntimeError("Embedding shard SHA-256 changed after registration")
        with safe_open(str(path), framework="np") as tensors:
            matrix = tensors.get_tensor("embeddings").astype(np.float32)
        if matrix.shape != (shard.rows, shard.dimensions):
            raise RuntimeError("Embedding shard tensor shape changed")
        return matrix

    def _directory(self, task_id: str) -> Path:
        if not task_id or any(character not in "0123456789abcdef-" for character in task_id):
            raise ValueError("Task id is unsafe for an embedding cache path")
        path = (self.project_root / "data" / "tasks" / task_id / "embeddings" / "siglip2")
        path = path.resolve(strict=False)
        path.relative_to(self.project_root)
        return path

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
