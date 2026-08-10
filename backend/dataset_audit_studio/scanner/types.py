from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DiscoveredMedia:
    absolute_path: Path
    relative_path: str
    source_size: int
    source_mtime_ns: int
    media_kind_hint: str
    artist_scope: str


@dataclass(frozen=True)
class DiscoveryResult:
    items: tuple[DiscoveredMedia, ...]
    ignored_reparse_count: int
    ignored_directory_count: int


@dataclass(frozen=True)
class MetricEvidence:
    code: str
    value: float | int | bool | str | None
    threshold: float | int | None
    severity: str
    review_only: bool
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolutionResult:
    resolution: int
    area_pixels: int
    minimum_area: int
    area_pass: bool
    bucket_width: int
    bucket_height: int
    upscale_factor: float
    crop_loss: float
    aspect_ratio: float
    eligible: bool
    risk_codes: tuple[str, ...]


@dataclass(frozen=True)
class ScannedMedia:
    relative_path: str
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    pixel_sha256: str | None
    media_kind: str
    artist_scope: str
    scan_state: str
    encoded_width: int | None
    encoded_height: int | None
    display_width: int | None
    display_height: int | None
    frame_count: int | None
    is_animated: bool
    exif_orientation: int | None
    extracted_frame_path: str | None
    export_requires_render: bool
    phash: str | None
    colorhash: str | None
    evidence: tuple[MetricEvidence, ...]
    resolutions: tuple[ResolutionResult, ...]


@dataclass(frozen=True)
class ManifestInfo:
    path: Path
    sha256: str
    item_count: int
    ignored_reparse_count: int
    ignored_directory_count: int


@dataclass(frozen=True)
class ScanSummary:
    task_id: str
    manifest_sha256: str
    discovered: int
    processed: int
    valid: int
    hard_rejected: int
    decode_errors: int
    source_changed: int
    resumed_from_index: int
    final_status: str
