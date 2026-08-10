from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class DatasetProfile(StrEnum):
    ARTIST_CONCEPT = "artist_concept"
    CHARACTER_CONCEPT = "character_concept"
    GENERAL = "general"


@dataclass(frozen=True)
class ProfileConstraints:
    policy_mode: str
    active_views: tuple[str, ...]
    scope_mode: str
    style_enabled: bool
    semantic_enabled: bool
    hierarchy_enabled: bool


PROFILE_CONSTRAINTS: Mapping[DatasetProfile, ProfileConstraints] = MappingProxyType(
    {
        DatasetProfile.ARTIST_CONCEPT: ProfileConstraints(
            policy_mode="report_only",
            active_views=("broad",),
            scope_mode="concept",
            style_enabled=True,
            semantic_enabled=False,
            hierarchy_enabled=False,
        ),
        DatasetProfile.CHARACTER_CONCEPT: ProfileConstraints(
            policy_mode="report_only",
            active_views=("broad",),
            scope_mode="concept",
            style_enabled=False,
            semantic_enabled=True,
            hierarchy_enabled=True,
        ),
        DatasetProfile.GENERAL: ProfileConstraints(
            policy_mode="report_only",
            active_views=("broad",),
            scope_mode="global",
            style_enabled=False,
            semantic_enabled=False,
            hierarchy_enabled=False,
        ),
    }
)

_PROFILE_OWNED_COMPONENT_IDS: Mapping[DatasetProfile, tuple[str, ...]] = MappingProxyType(
    {
        DatasetProfile.ARTIST_CONCEPT: ("style.artist",),
        DatasetProfile.CHARACTER_CONCEPT: (
            "style.artist",
            "embedding.semantic",
            "cluster.hierarchy",
        ),
        DatasetProfile.GENERAL: ("style.artist",),
    }
)

_PROFILE_OWNED_CONFIG_FIELDS: Mapping[
    DatasetProfile, Mapping[str, tuple[str, ...]]
] = MappingProxyType(
    {
        DatasetProfile.ARTIST_CONCEPT: MappingProxyType(
            {"style.artist": ("enabled",)}
        ),
        DatasetProfile.CHARACTER_CONCEPT: MappingProxyType(
            {
                "style.artist": ("enabled",),
                "cluster.hierarchy": ("scope_mode",),
            }
        ),
        DatasetProfile.GENERAL: MappingProxyType(
            {"style.artist": ("enabled",)}
        ),
    }
)

PROFILE_DEFAULT_DISABLED_COMPONENT_IDS = (
    "score.aesthetic_domain",
    "detect.ai",
    "evidence.ocr",
    "evidence.watermark",
    "analysis.sae",
    "embedding.semantic",
    "cluster.hierarchy",
    "latent.resolve",
)


def resolve_dataset_profile(value: DatasetProfile | str) -> DatasetProfile:
    if isinstance(value, DatasetProfile):
        return value
    try:
        return DatasetProfile(value)
    except ValueError as error:
        supported = ", ".join(item.value for item in DatasetProfile)
        message = f"Unknown dataset profile {value!r}; expected one of: {supported}"
        raise ValueError(message) from error


def profile_constraints(value: DatasetProfile | str) -> ProfileConstraints:
    return PROFILE_CONSTRAINTS[resolve_dataset_profile(value)]


def profile_owned_component_ids(value: DatasetProfile | str) -> tuple[str, ...]:
    return _PROFILE_OWNED_COMPONENT_IDS[resolve_dataset_profile(value)]


def profile_owned_config_fields(
    value: DatasetProfile | str,
) -> Mapping[str, tuple[str, ...]]:
    return _PROFILE_OWNED_CONFIG_FIELDS[resolve_dataset_profile(value)]
