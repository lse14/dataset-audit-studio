from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import torch
from safetensors import safe_open
from safetensors.torch import save_file

from dataset_audit_studio.clustering.config import SAEConfig
from dataset_audit_studio.clustering.types import SAEAnalysis, SAEArtifact
from dataset_audit_studio.runtime import PROJECT_ROOT


class SAEStore:
    def __init__(self, *, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root.resolve(strict=False)

    @staticmethod
    def cache_key(shard_hashes: tuple[str, ...], config: SAEConfig) -> str:
        payload = {
            "schema": "siglip_sae_v1",
            "shards": shard_hashes,
            "config": config.model_dump(mode="json"),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()

    def write(
        self,
        *,
        task_id: str,
        cache_key: str,
        sample_ids: tuple[str, ...],
        analysis: SAEAnalysis,
    ) -> SAEArtifact:
        if analysis.activations.shape[0] != len(sample_ids):
            raise ValueError("SAE activations do not match sample ids")
        directory = self._directory(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / f"{cache_key}.safetensors"
        part = final.with_suffix(final.suffix + ".part")
        tensors = {
            f"model.{key}": value.detach().cpu().contiguous()
            for key, value in analysis.state_dict.items()
        }
        tensors["activations"] = torch.from_numpy(
            analysis.activations.astype(np.float16)
        )
        tensors["thresholds"] = torch.from_numpy(
            analysis.thresholds.astype(np.float32)
        )
        metadata = {
            "schema": "siglip_sae_v1",
            "sample_ids_json": json.dumps(sample_ids, separators=(",", ":")),
            "top_indices_json": json.dumps(analysis.top_indices, separators=(",", ":")),
            "losses_json": json.dumps(analysis.losses, separators=(",", ":")),
        }
        save_file(tensors, str(part), metadata=metadata)
        with part.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(part, final)
        return self.inspect(task_id=task_id, cache_key=cache_key)

    def inspect(self, *, task_id: str, cache_key: str) -> SAEArtifact:
        path = (self._directory(task_id) / f"{cache_key}.safetensors").resolve(
            strict=True
        )
        path.relative_to(self.project_root)
        with safe_open(str(path), framework="pt", device="cpu") as tensors:
            metadata = dict(tensors.metadata() or {})
            if metadata.get("schema") != "siglip_sae_v1":
                raise RuntimeError("SAE artifact has an unsupported schema")
            activations = tensors.get_tensor("activations")
            thresholds = tensors.get_tensor("thresholds")
            encoder = tensors.get_tensor("model.encoder.weight")
        sample_ids = tuple(json.loads(metadata["sample_ids_json"]))
        top_indices = tuple(
            tuple(int(index) for index in values)
            for values in json.loads(metadata["top_indices_json"])
        )
        losses = tuple(float(value) for value in json.loads(metadata["losses_json"]))
        if activations.shape[0] != len(sample_ids):
            raise RuntimeError("SAE artifact sample metadata does not match activations")
        if activations.shape[1] != len(thresholds) or encoder.shape[0] != len(thresholds):
            raise RuntimeError("SAE artifact feature dimensions are inconsistent")
        stat = path.stat()
        return SAEArtifact(
            cache_key=cache_key,
            relative_path=path.relative_to(self.project_root).as_posix(),
            sha256=self._sha256(path),
            size_bytes=stat.st_size,
            sample_ids=sample_ids,
            input_dimensions=int(encoder.shape[1]),
            feature_count=int(encoder.shape[0]),
            thresholds=tuple(float(value) for value in thresholds.tolist()),
            top_indices=top_indices,
            losses=losses,
        )

    def try_inspect(self, *, task_id: str, cache_key: str) -> SAEArtifact | None:
        try:
            return self.inspect(task_id=task_id, cache_key=cache_key)
        except FileNotFoundError:
            return None

    def load_activations(self, artifact: SAEArtifact) -> np.ndarray:
        path = self.project_root.joinpath(*Path(artifact.relative_path).parts).resolve(
            strict=True
        )
        path.relative_to(self.project_root)
        if self._sha256(path) != artifact.sha256:
            raise RuntimeError("SAE artifact SHA-256 changed after registration")
        with safe_open(str(path), framework="pt", device="cpu") as tensors:
            values = tensors.get_tensor("activations").float().numpy()
        if values.shape != (len(artifact.sample_ids), artifact.feature_count):
            raise RuntimeError("SAE activation shape changed")
        return values

    def _directory(self, task_id: str) -> Path:
        if not task_id or any(character not in "0123456789abcdef-" for character in task_id):
            raise ValueError("Task id is unsafe for an SAE cache path")
        path = (self.project_root / "data" / "tasks" / task_id / "embeddings" / "sae")
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
