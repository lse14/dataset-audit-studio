from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import dataset_audit_studio.core.profile_contracts as _profile_contracts

__all__ = (
    "BuiltinProfileSpec",
    "PROFILE_SPECS",
    "apply_profile",
    "profile_from_components",
    "profile_spec",
)


@dataclass(frozen=True)
class BuiltinProfileSpec:
    id: _profile_contracts.DatasetProfile
    display_name: str
    description: str
    scope_mode: str
    style_enabled: bool


PROFILE_SPECS: tuple[BuiltinProfileSpec, ...] = (
    BuiltinProfileSpec(
        id=_profile_contracts.DatasetProfile.ARTIST_CONCEPT,
        display_name="Artist concept",
        description="Per-folder style consistency evidence with content diversity retained.",
        scope_mode=_profile_contracts.profile_constraints(
            _profile_contracts.DatasetProfile.ARTIST_CONCEPT
        ).scope_mode,
        style_enabled=_profile_contracts.profile_constraints(
            _profile_contracts.DatasetProfile.ARTIST_CONCEPT
        ).style_enabled,
    ),
    BuiltinProfileSpec(
        id=_profile_contracts.DatasetProfile.CHARACTER_CONCEPT,
        display_name="Character concept",
        description="Pre-grouped concept inputs with style diversity retained.",
        scope_mode=_profile_contracts.profile_constraints(
            _profile_contracts.DatasetProfile.CHARACTER_CONCEPT
        ).scope_mode,
        style_enabled=_profile_contracts.profile_constraints(
            _profile_contracts.DatasetProfile.CHARACTER_CONCEPT
        ).style_enabled,
    ),
    BuiltinProfileSpec(
        id=_profile_contracts.DatasetProfile.GENERAL,
        display_name="General dataset",
        description="Global semantic coverage and long-tail reporting.",
        scope_mode=_profile_contracts.profile_constraints(
            _profile_contracts.DatasetProfile.GENERAL
        ).scope_mode,
        style_enabled=_profile_contracts.profile_constraints(
            _profile_contracts.DatasetProfile.GENERAL
        ).style_enabled,
    ),
)


def profile_spec(value: _profile_contracts.DatasetProfile | str) -> BuiltinProfileSpec:
    profile = _profile_contracts.resolve_dataset_profile(value)
    return next(item for item in PROFILE_SPECS if item.id == profile)


def profile_from_components(
    components: Mapping[str, Any],
    *,
    require_profile: bool,
) -> _profile_contracts.DatasetProfile | None:
    value = components.get("profile")
    if value is None:
        if require_profile:
            raise ValueError(
                "New component-form tasks require a dataset profile"
            )
        return None
    if not isinstance(value, str):
        raise TypeError("Dataset profile must be a string")
    return _profile_contracts.resolve_dataset_profile(value)


def apply_profile(
    components: Mapping[str, Any],
    profile: _profile_contracts.DatasetProfile | str,
) -> dict[str, Any]:
    resolved = _profile_contracts.resolve_dataset_profile(profile)
    constraints = _profile_contracts.profile_constraints(resolved)
    result = copy.deepcopy(dict(components))

    style = _component_entry(result, "style.artist")
    style["enabled"] = constraints.style_enabled
    _component_config(result, "style.artist")["enabled"] = constraints.style_enabled

    for component_id, enabled in (
        ("embedding.semantic", constraints.semantic_enabled),
        ("cluster.hierarchy", constraints.hierarchy_enabled),
    ):
        if enabled:
            _component_entry(result, component_id)["enabled"] = True

    _component_config(result, "cluster.hierarchy")["scope_mode"] = constraints.scope_mode

    aesthetic_bins = _component_config(result, "export.dataset").get(
        "aesthetic_bins", "disabled"
    )
    if aesthetic_bins == "score_x2_floor":
        if resolved != _profile_contracts.DatasetProfile.GENERAL:
            raise ValueError("Aesthetic bins are only supported for the general profile")
        _component_entry(result, "score.aesthetic_domain")["enabled"] = True
    return result


def _component_entry(components: Mapping[str, Any], component_id: str) -> dict[str, Any]:
    entry = components.get(component_id)
    if not isinstance(entry, dict):
        raise TypeError(f"Component config {component_id} must be an object")
    return entry


def _component_config(components: Mapping[str, Any], component_id: str) -> dict[str, Any]:
    config = _component_entry(components, component_id).get("config")
    if not isinstance(config, dict):
        raise TypeError(f"Component config {component_id}.config must be an object")
    return config
