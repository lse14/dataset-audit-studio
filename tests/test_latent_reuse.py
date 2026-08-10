from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest
from dataset_audit_studio.latent.config import LatentConfig, SingleLatentRule
from dataset_audit_studio.latent.mikazuki import plan_mikazuki_namespace
from dataset_audit_studio.latent.single_file import plan_single_file_latents
from dataset_audit_studio.latent.types import LatentSample
from pydantic import ValidationError
from safetensors.numpy import save_file


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sample(path: Path, root: Path, sample_id: str, *, render: bool = False) -> LatentSample:
    stat = path.stat()
    return LatentSample(
        sample_id=sample_id,
        relative_path=path.relative_to(root).as_posix(),
        source_path=path,
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        source_sha256=_sha256(path),
        export_requires_render=render,
    )


def _entry(path: Path, entry_key: str, shard: str) -> dict:
    stat = path.stat()
    return {
        "alpha_mask": False,
        "bucket_reso": [1024, 1024],
        "entry_key": entry_key,
        "flip_aug": False,
        "image_key": f"{path.name}#orig=64x64#bucket=1024x1024#flip=0#alpha=0",
        "image_size": [64, 64],
        "path": shard,
        "source_mtime_ns": stat.st_mtime_ns,
        "source_size": stat.st_size,
    }


def _write_mikazuki(root: Path, images: tuple[Path, ...]) -> Path:
    cache = root / ".mikazuki-cache" / "latents" / "anima"
    cache.mkdir(parents=True)
    shard = "no0001__2imgs__bucket_1024x1024__flip0__alpha0.safetensors"
    entries = [_entry(path, f"{index:08d}", shard) for index, path in enumerate(images)]
    catalog = {
        "entries": entries,
        "entry_count": len(entries),
        "format": "mikazuki_latents_catalog",
        "format_version": 1,
        "namespace": "anima",
    }
    (cache / "_catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    sidecar_entries = [
        {key: value for key, value in entry.items() if key != "path"}
        for entry in entries
    ]
    sidecar = {
        "format": "mikazuki_latents_safetensors_shard",
        "format_version": 1,
        "namespace": "anima",
        "shard_file": shard,
        "bucket_reso": [1024, 1024],
        "flip_aug": False,
        "alpha_mask": False,
        "sequence_no": 1,
        "image_count": len(entries),
        "cache_dtype": "auto",
        "entries": sidecar_entries,
    }
    (cache / f"{Path(shard).stem}.json").write_text(
        json.dumps(sidecar),
        encoding="utf-8",
    )
    tensors = {}
    for entry in entries:
        key = entry["entry_key"]
        tensors[f"latents::{key}"] = np.zeros((4, 8, 8), dtype=np.float16)
        tensors[f"original_size::{key}"] = np.asarray((64, 64), dtype=np.int64)
        tensors[f"crop_ltrb::{key}"] = np.asarray((0, 0, 64, 64), dtype=np.int64)
    save_file(tensors, str(cache / shard))
    return cache


def test_mikazuki_reuses_whole_shard_and_rebuilds_catalog_for_copied_shards(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    first = source / "first.webp"
    second = source / "second.webp"
    first.write_bytes(b"first-image")
    second.write_bytes(b"second-image")
    cache = _write_mikazuki(source, (first, second))
    plan = plan_mikazuki_namespace(
        source,
        (_sample(first, source, "first"),),
        namespace="anima",
    )
    assert {copy.kind for copy in plan.copies} == {
        "mikazuki_shard",
        "mikazuki_sidecar",
    }
    assert all(_sha256(copy.source_path) == copy.sha256 for copy in plan.copies)
    assert len(plan.records) == 1
    assert plan.records[0].image_sha256 == _sha256(first)
    rebuilt = json.loads(plan.catalogs[0].content)
    assert rebuilt["entry_count"] == 2
    assert {entry["image_key"].split("#", 1)[0] for entry in rebuilt["entries"]} == {
        "first.webp",
        "second.webp",
    }
    assert plan.catalogs[0].destination_relative == (
        ".mikazuki-cache/latents/anima/_catalog.json"
    )
    assert (cache / rebuilt["entries"][0]["path"]).is_file()


def test_mikazuki_does_not_reuse_when_source_changed_or_export_renders(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    image = source / "image.webp"
    image.write_bytes(b"image")
    sample = _sample(image, source, "sample")
    _write_mikazuki(source, (image,))
    stat = image.stat()
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    changed = plan_mikazuki_namespace(source, (sample,), namespace="anima")
    assert changed.copies == ()

    current = _sample(image, source, "sample", render=True)
    rendered = plan_mikazuki_namespace(source, (current,), namespace="anima")
    assert rendered.copies == ()


def test_explicit_single_file_rule_never_collects_unmatched_safetensors(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    artist = source / "artist"
    artist.mkdir(parents=True)
    image = artist / "image.webp"
    image.write_bytes(b"image")
    latent = artist / "image.npz"
    latent.write_bytes(b"latent")
    (artist / "weights.safetensors").write_bytes(b"model-weights")
    rule = SingleLatentRule(name="aitoolkit_npz", pattern="{stem}.npz")
    plan = plan_single_file_latents(
        source,
        (_sample(image, source, "sample"),),
        (rule,),
    )
    assert [copy.destination_relative for copy in plan.copies] == ["artist/image.npz"]
    assert plan.records[0].source_path == "artist/image.npz"
    assert plan.records[0].image_sha256 == _sha256(image)


def test_latent_config_requires_explicit_safe_single_file_patterns() -> None:
    config = LatentConfig.from_task_config({})
    assert config.mikazuki_enabled is True
    assert config.single_file_rules == ()
    with pytest.raises(ValidationError, match="image directory"):
        SingleLatentRule(name="unsafe", pattern="../{stem}.npz")
    with pytest.raises(ValidationError, match=".npz or .safetensors"):
        SingleLatentRule(name="unsafe", pattern="{stem}.pt")
