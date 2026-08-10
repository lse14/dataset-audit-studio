from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class LatentCopyReference(_ArtifactModel):
    source_relative: str
    destination_relative: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    kind: str


class LatentCatalogReference(_ArtifactModel):
    destination_relative: str
    content: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LatentRecordReference(_ArtifactModel):
    sample_id: str
    cache_kind: str
    source_path: str
    namespace: str | None
    shard_path: str | None
    entry_key: str | None
    image_sha256: str
    compatibility: dict
    metadata: dict


class LatentDatasetReference(_ArtifactModel):
    stage: int = Field(ge=1, le=3)
    resolution: int = Field(gt=0)
    copies: tuple[LatentCopyReference, ...]
    catalogs: tuple[LatentCatalogReference, ...]
    records: tuple[LatentRecordReference, ...]


class LatentReferenceArtifact(_ArtifactModel):
    schema_name: Literal["latent.reference.v1"] = Field(
        default="latent.reference.v1",
        alias="schema",
    )
    task_id: str
    config_hash: str
    datasets: tuple[LatentDatasetReference, ...]


@dataclass(frozen=True)
class DatasetSample:
    sample_id: str
    relative_path: str
    artist_scope: str
    source_path: Path
    image_path: Path
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    pixel_sha256: str
    export_requires_render: bool


@dataclass(frozen=True)
class DatasetSlice:
    stage: int
    resolution: int
    sample_ids: tuple[str, ...]


@dataclass(frozen=True)
class DatasetWorkspace:
    samples: tuple[DatasetSample, ...]
    datasets: tuple[DatasetSlice, ...]
