from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class SemanticSample:
    sample_id: str
    relative_path: str
    artist_scope: str
    source_path: Path
    image_path: Path
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    pixel_sha256: str


@dataclass(frozen=True)
class SemanticEmbeddingBatch:
    sample_ids: tuple[str, ...]
    embeddings: np.ndarray
