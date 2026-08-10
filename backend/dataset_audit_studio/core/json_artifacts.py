from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JsonArtifact:
    producer_id: str
    kind: str
    cache_key: str
    relative_path: str
    sha256: str
    size_bytes: int
