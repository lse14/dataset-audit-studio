from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
from safetensors import safe_open
from safetensors.numpy import save_file

from dataset_audit_studio.core.feature_batch import FeatureBatch
from dataset_audit_studio.core.feature_store import FeatureShard
from dataset_audit_studio.runtime import PROJECT_ROOT

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SafetensorFeatureStore:
    def __init__(self, *, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve(strict=False)

    def cache_key(
        self,
        *,
        sample_ids: tuple[str, ...],
        pixel_hashes: tuple[str, ...],
        capabilities: tuple[str, ...],
        model_digest: str,
        preprocessing_version: str,
    ) -> str:
        if len(sample_ids) != len(pixel_hashes):
            raise ValueError("Feature shard ids and pixel hashes must have equal lengths")
        if not capabilities or len(capabilities) != len(set(capabilities)):
            raise ValueError("Feature shard capabilities must be non-empty and unique")
        self._require_sha(model_digest, "model digest")
        digest = hashlib.sha256()
        digest.update(f"{model_digest}\0{preprocessing_version}\n".encode())
        for capability in sorted(capabilities):
            digest.update(f"{capability}\n".encode())
        for sample_id, pixel_hash in zip(sample_ids, pixel_hashes, strict=True):
            self._require_sha(pixel_hash, "pixel hash")
            digest.update(f"{sample_id}\0{pixel_hash}\n".encode())
        return digest.hexdigest()

    def write(
        self,
        *,
        task_id: str,
        producer_id: str,
        sample_ids: tuple[str, ...],
        pixel_hashes: tuple[str, ...],
        features: dict[str, np.ndarray],
        model_digest: str,
        preprocessing_version: str,
    ) -> FeatureShard:
        capabilities = tuple(sorted(features))
        cache_key = self.cache_key(
            sample_ids=sample_ids,
            pixel_hashes=pixel_hashes,
            capabilities=capabilities,
            model_digest=model_digest,
            preprocessing_version=preprocessing_version,
        )
        tensors: dict[str, np.ndarray] = {}
        for capability in capabilities:
            matrix = np.ascontiguousarray(features[capability], dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape[0] != len(sample_ids):
                raise ValueError(f"Feature {capability} shape does not match sample ids")
            if np.any(~np.isfinite(matrix)):
                raise ValueError(f"Feature {capability} contains non-finite values")
            tensors[capability] = matrix
        directory = self._directory(task_id, producer_id)
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / f"{cache_key}.safetensors"
        part = final.with_suffix(".safetensors.part")
        metadata = {
            "schema": "component_feature_shard_v1",
            "producer_id": producer_id,
            "sample_ids_json": json.dumps(sample_ids, separators=(",", ":")),
            "pixel_hashes_json": json.dumps(pixel_hashes, separators=(",", ":")),
            "capabilities_json": json.dumps(capabilities, separators=(",", ":")),
            "model_digest": model_digest,
            "preprocessing_version": preprocessing_version,
        }
        save_file(tensors, str(part), metadata=metadata)
        with part.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(part, final)
        return self.inspect(task_id=task_id, producer_id=producer_id, cache_key=cache_key)

    def inspect(self, *, task_id: str, producer_id: str, cache_key: str) -> FeatureShard:
        self._require_sha(cache_key, "cache key")
        path = (self._directory(task_id, producer_id) / f"{cache_key}.safetensors").resolve(
            strict=True
        )
        path.relative_to(self.project_root)
        with safe_open(str(path), framework="np") as tensors:
            metadata = dict(tensors.metadata() or {})
            if metadata.get("schema") != "component_feature_shard_v1":
                raise RuntimeError("Feature shard has an unsupported schema")
            capabilities = tuple(json.loads(metadata["capabilities_json"]))
            dimensions = []
            rows = None
            for capability in capabilities:
                matrix = tensors.get_tensor(capability)
                if matrix.ndim != 2:
                    raise RuntimeError(f"Feature {capability} is not a matrix")
                rows = matrix.shape[0] if rows is None else rows
                if matrix.shape[0] != rows:
                    raise RuntimeError("Feature shard tensors have different row counts")
                dimensions.append((capability, int(matrix.shape[1])))
        sample_ids = tuple(json.loads(metadata["sample_ids_json"]))
        pixel_hashes = tuple(json.loads(metadata["pixel_hashes_json"]))
        if rows != len(sample_ids) or len(pixel_hashes) != len(sample_ids):
            raise RuntimeError("Feature shard metadata does not match tensor rows")
        if metadata.get("producer_id") != producer_id:
            raise RuntimeError("Feature shard producer identity changed")
        stat = path.stat()
        return FeatureShard(
            producer_id=producer_id,
            cache_key=cache_key,
            relative_path=path.relative_to(self.project_root).as_posix(),
            sample_ids=sample_ids,
            pixel_hashes=pixel_hashes,
            capabilities=capabilities,
            dimensions=tuple(dimensions),
            model_digest=metadata["model_digest"],
            preprocessing_version=metadata["preprocessing_version"],
            sha256=self._sha256(path),
            size_bytes=stat.st_size,
        )

    def try_inspect(
        self,
        *,
        task_id: str,
        producer_id: str,
        cache_key: str,
    ) -> FeatureShard | None:
        try:
            return self.inspect(
                task_id=task_id,
                producer_id=producer_id,
                cache_key=cache_key,
            )
        except FileNotFoundError:
            return None

    def load(self, shard: FeatureShard) -> FeatureBatch:
        path = self.project_root.joinpath(*Path(shard.relative_path).parts).resolve(strict=True)
        path.relative_to(self.project_root)
        if self._sha256(path) != shard.sha256:
            raise RuntimeError("Feature shard SHA-256 changed after registration")
        with safe_open(str(path), framework="np") as tensors:
            features = {
                capability: tensors.get_tensor(capability).astype(np.float32)
                for capability in shard.capabilities
            }
        return FeatureBatch.create(shard.sample_ids, features)

    def _directory(self, task_id: str, producer_id: str) -> Path:
        if not task_id or any(character not in "0123456789abcdef-" for character in task_id):
            raise ValueError("Task id is unsafe for a feature cache path")
        if not _SAFE_ID.fullmatch(producer_id):
            raise ValueError(f"Unsafe feature producer id: {producer_id}")
        path = (
            self.project_root
            / "data"
            / "tasks"
            / task_id
            / "features"
            / producer_id.replace(".", "_")
        ).resolve(strict=False)
        path.relative_to(self.project_root)
        return path

    @staticmethod
    def _require_sha(value: str, label: str) -> None:
        if not _SHA256.fullmatch(value):
            raise ValueError(f"Invalid {label}: {value!r}")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

