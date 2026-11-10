from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
