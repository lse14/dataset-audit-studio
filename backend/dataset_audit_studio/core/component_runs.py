from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ComponentRunView:
    component_id: str
    component_version: str
    phase: str
    phase_order: int
    execution: str
    status: str
    config_hash: str
    config_digest: str
    input_digest: str | None
    model_digest: str | None
    normalized_config: dict
    dependency_ids: tuple[str, ...]
    model_ids: tuple[str, ...]
    checkpoint: dict
    completed_items: int
    total_items: int | None
    auto_enabled: bool
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
