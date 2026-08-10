from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LatentSample:
    sample_id: str
    relative_path: str
    source_path: Path
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    export_requires_render: bool


@dataclass(frozen=True)
class LatentCopy:
    source_path: Path
    destination_relative: str
    sha256: str
    size_bytes: int
    kind: str


@dataclass(frozen=True)
class LatentRecord:
    sample_id: str
    cache_kind: str
    source_path: str
    namespace: str | None
    shard_path: str | None
    entry_key: str | None
    image_sha256: str
    compatibility: dict
    metadata: dict


@dataclass(frozen=True)
class MikazukiCatalogOutput:
    destination_relative: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class LatentPlan:
    copies: tuple[LatentCopy, ...]
    catalogs: tuple[MikazukiCatalogOutput, ...]
    records: tuple[LatentRecord, ...]
