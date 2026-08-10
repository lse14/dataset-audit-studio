from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from safetensors import safe_open

from dataset_audit_studio.components.latent_resolver.common import (
    require_regular_file,
    sha256_file,
    source_is_unchanged,
)
from dataset_audit_studio.components.latent_resolver.contracts import (
    LatentCopy,
    LatentPlan,
    LatentRecord,
    LatentSample,
    MikazukiCatalogOutput,
)


class _EntryBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    alpha_mask: bool
    bucket_reso: tuple[int, int]
    entry_key: str = Field(min_length=1, max_length=160)
    flip_aug: bool
    image_key: str = Field(min_length=1, max_length=32_767)
    image_size: tuple[int, int]
    source_mtime_ns: int = Field(ge=0)
    source_size: int = Field(ge=0)


class _CatalogEntry(_EntryBase):
    path: str = Field(min_length=1, max_length=255)


class _SidecarEntry(_EntryBase):
    pass


class _Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[_CatalogEntry, ...]
    entry_count: int = Field(ge=0)
    format: Literal["mikazuki_latents_catalog"]
    format_version: Literal[1]
    namespace: str


class _Sidecar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["mikazuki_latents_safetensors_shard"]
    format_version: Literal[1]
    namespace: str
    shard_file: str
    bucket_reso: tuple[int, int]
    flip_aug: bool
    alpha_mask: bool
    sequence_no: int = Field(ge=0)
    image_count: int = Field(ge=0)
    cache_dtype: str
    entries: tuple[_SidecarEntry, ...]


def _safe_shard_name(value: str) -> str:
    pure = PurePosixPath(value)
    if (
        pure.name != value
        or pure.is_absolute()
        or ".." in pure.parts
        or pure.suffix.casefold() != ".safetensors"
        or value.startswith(".")
    ):
        raise RuntimeError(f"Unsafe Mikazuki shard path: {value}")
    return value


def _image_path_from_key(image_key: str) -> str:
    raw = image_key.split("#", 1)[0].replace("\\", "/").strip("/")
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise RuntimeError(f"Unsafe Mikazuki image key: {image_key}")
    return pure.as_posix()


def _read_json(path: Path, *, maximum_bytes: int) -> object:
    if path.stat().st_size > maximum_bytes:
        raise RuntimeError(f"Mikazuki JSON exceeds the size limit: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid Mikazuki JSON {path.name}: {error}") from error


def _sample_for_entry(
    entry: _CatalogEntry,
    *,
    exact: dict[str, LatentSample],
    unique_names: dict[str, LatentSample],
) -> LatentSample | None:
    image_path = _image_path_from_key(entry.image_key)
    sample = exact.get(image_path.casefold())
    if sample is None and "/" not in image_path:
        sample = unique_names.get(image_path.casefold())
    if sample is None:
        return None
    if (
        sample.source_size != entry.source_size
        or sample.source_mtime_ns != entry.source_mtime_ns
    ):
        return None
    return sample


def plan_mikazuki_namespace(
    source_root: Path,
    samples: tuple[LatentSample, ...],
    *,
    namespace: str,
    verified_sample_ids: frozenset[str] = frozenset(),
) -> LatentPlan:
    source_root = source_root.resolve(strict=True)
    cache_root = (
        source_root / ".mikazuki-cache" / "latents" / namespace
    )
    catalog_path = cache_root / "_catalog.json"
    if not catalog_path.is_file():
        return LatentPlan(copies=(), catalogs=(), records=())
    require_regular_file(catalog_path, source_root)
    try:
        catalog = _Catalog.model_validate(_read_json(catalog_path, maximum_bytes=64 << 20))
    except ValidationError as error:
        raise RuntimeError(f"Invalid Mikazuki catalog schema: {error}") from error
    if catalog.namespace != namespace:
        raise RuntimeError("Mikazuki catalog namespace does not match its directory")
    if catalog.entry_count != len(catalog.entries) or len(catalog.entries) > 1_000_000:
        raise RuntimeError("Mikazuki catalog entry_count is inconsistent or excessive")

    exact = {sample.relative_path.casefold(): sample for sample in samples}
    name_counts = Counter(Path(sample.relative_path).name.casefold() for sample in samples)
    unique_names = {
        Path(sample.relative_path).name.casefold(): sample
        for sample in samples
        if name_counts[Path(sample.relative_path).name.casefold()] == 1
    }
    selected_entries: dict[tuple[str, str], tuple[_CatalogEntry, LatentSample]] = {}
    entries_by_shard: dict[str, list[_CatalogEntry]] = defaultdict(list)
    unchanged: dict[str, bool] = {}
    for entry in catalog.entries:
        shard_name = _safe_shard_name(entry.path)
        entries_by_shard[shard_name].append(entry)
        sample = _sample_for_entry(entry, exact=exact, unique_names=unique_names)
        if sample is None:
            continue
        if sample.sample_id not in unchanged:
            unchanged[sample.sample_id] = (
                sample.sample_id in verified_sample_ids or source_is_unchanged(sample)
            )
        if unchanged[sample.sample_id]:
            selected_entries[(shard_name, entry.entry_key)] = (entry, sample)

    selected_shards = sorted({shard for shard, _ in selected_entries})
    if not selected_shards:
        return LatentPlan(copies=(), catalogs=(), records=())
    copies: list[LatentCopy] = []
    copied_entries: list[_CatalogEntry] = []
    records: list[LatentRecord] = []
    prefix = PurePosixPath(".mikazuki-cache") / "latents" / namespace
    for shard_name in selected_shards:
        shard_path = require_regular_file(cache_root / shard_name, source_root)
        sidecar_path = require_regular_file(
            cache_root / f"{Path(shard_name).stem}.json",
            source_root,
        )
        try:
            sidecar = _Sidecar.model_validate(
                _read_json(sidecar_path, maximum_bytes=16 << 20)
            )
        except ValidationError as error:
            raise RuntimeError(f"Invalid Mikazuki sidecar schema: {error}") from error
        if (
            sidecar.namespace != namespace
            or sidecar.shard_file != shard_name
            or sidecar.image_count != len(sidecar.entries)
        ):
            raise RuntimeError(f"Mikazuki sidecar identity is inconsistent: {sidecar_path.name}")
        catalog_identity = {
            (entry.image_key, entry.entry_key) for entry in entries_by_shard[shard_name]
        }
        sidecar_identity = {(entry.image_key, entry.entry_key) for entry in sidecar.entries}
        if catalog_identity != sidecar_identity:
            raise RuntimeError(f"Mikazuki catalog and sidecar disagree: {shard_name}")
        with safe_open(str(shard_path), framework="np") as tensors:
            keys = set(tensors.keys())
        for entry in sidecar.entries:
            expected = {
                f"latents::{entry.entry_key}",
                f"original_size::{entry.entry_key}",
                f"crop_ltrb::{entry.entry_key}",
            }
            if not expected.issubset(keys):
                raise RuntimeError(
                    f"Mikazuki shard is missing tensors for entry {entry.entry_key}"
                )
        for path, kind in ((shard_path, "mikazuki_shard"), (sidecar_path, "mikazuki_sidecar")):
            relative = (prefix / path.name).as_posix()
            copies.append(
                LatentCopy(
                    source_path=path,
                    destination_relative=relative,
                    sha256=sha256_file(path),
                    size_bytes=path.stat().st_size,
                    kind=kind,
                )
            )
        copied_entries.extend(entries_by_shard[shard_name])
        for entry in entries_by_shard[shard_name]:
            selected = selected_entries.get((shard_name, entry.entry_key))
            if selected is None:
                continue
            _, sample = selected
            shard_relative = (prefix / shard_name).as_posix()
            records.append(
                LatentRecord(
                    sample_id=sample.sample_id,
                    cache_kind="mikazuki_shard",
                    source_path=shard_relative,
                    namespace=namespace,
                    shard_path=shard_relative,
                    entry_key=entry.entry_key,
                    image_sha256=sample.source_sha256,
                    compatibility={
                        "bucket_reso": list(entry.bucket_reso),
                        "flip_aug": entry.flip_aug,
                        "alpha_mask": entry.alpha_mask,
                        "namespace": namespace,
                    },
                    metadata={
                        "image_key": entry.image_key,
                        "source_size": entry.source_size,
                        "source_mtime_ns": entry.source_mtime_ns,
                    },
                )
            )
    catalog_payload = {
        "entries": [entry.model_dump(mode="json") for entry in copied_entries],
        "entry_count": len(copied_entries),
        "format": catalog.format,
        "format_version": catalog.format_version,
        "namespace": namespace,
    }
    content = (
        json.dumps(
            catalog_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    destination = (prefix / "_catalog.json").as_posix()
    return LatentPlan(
        copies=tuple(copies),
        catalogs=(
            MikazukiCatalogOutput(
                destination_relative=destination,
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
            ),
        ),
        records=tuple(records),
    )
