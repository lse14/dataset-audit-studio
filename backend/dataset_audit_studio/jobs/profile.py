from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dataset_audit_studio.jobs.errors import LegacyTaskConfigUnsupported

BUILTIN_PROFILE_IDS = frozenset(
    {
        "artist_concept",
        "character_concept",
        "general",
    }
)



def has_builtin_profile(task_config: Mapping[str, Any]) -> bool:
    return task_config.get("profile") in BUILTIN_PROFILE_IDS


def require_builtin_profile(task_config: Mapping[str, Any]) -> None:
    if not has_builtin_profile(task_config):
        raise LegacyTaskConfigUnsupported(
            "legacy_task_config_unsupported: profile-free tasks cannot execute"
        )

