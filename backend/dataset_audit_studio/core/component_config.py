from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dataset_audit_studio.core.component_contracts import NormalizedComponentConfig


@dataclass(frozen=True)
class ComponentConfigNormalizer:
    """Normalize one entry from the canonical component-form task payload."""

    component_id: str

    def __call__(self, task_config: Mapping[str, Any]) -> NormalizedComponentConfig:
        components = task_config.get("components")
        if not isinstance(components, Mapping):
            raise ValueError("Task config must contain a complete components object")
        entry = components.get(self.component_id)
        if not isinstance(entry, Mapping):
            raise ValueError(f"Missing component config: {self.component_id}")
        enabled = entry.get("enabled")
        config = entry.get("config")
        if not isinstance(enabled, bool):
            raise TypeError(f"Component config {self.component_id}.enabled must be boolean")
        if not isinstance(config, Mapping):
            raise TypeError(f"Component config {self.component_id}.config must be an object")
        extra = sorted(set(entry) - {"enabled", "config"})
        if extra:
            raise ValueError(
                f"Component config {self.component_id} has unknown keys: {extra}"
            )
        return NormalizedComponentConfig(
            component_id=self.component_id,
            enabled=enabled,
            config=copy.deepcopy(dict(config)),
        )
