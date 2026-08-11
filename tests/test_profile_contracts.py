from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, is_dataclass
from importlib import import_module
from pathlib import Path

import pytest
from dataset_audit_studio.presets.builtin import PROFILE_SPECS


def _contracts():
    return import_module("dataset_audit_studio.core.profile_contracts")


def test_core_profile_contract_preserves_enum_values_and_constraints() -> None:
    contracts = _contracts()

    assert [item.value for item in contracts.DatasetProfile] == [
        "artist_concept",
        "character_concept",
        "general",
    ]
    expected = {
        contracts.DatasetProfile.ARTIST_CONCEPT: ("concept", True),
        contracts.DatasetProfile.CHARACTER_CONCEPT: ("concept", False),
        contracts.DatasetProfile.GENERAL: ("global", False),
    }
    assert tuple(contracts.PROFILE_CONSTRAINTS) == tuple(expected)

    for profile, (scope_mode, style_enabled) in expected.items():
        constraints = contracts.profile_constraints(profile)
        assert is_dataclass(constraints)
        assert constraints.scope_mode == scope_mode
        assert constraints.style_enabled is style_enabled
        assert constraints.semantic_enabled is True
        assert constraints.hierarchy_enabled is True
        assert constraints.policy_mode == "report_only"
        assert constraints.active_views == ("broad",)
        with pytest.raises(FrozenInstanceError):
            constraints.scope_mode = "global"

    with pytest.raises(
        ValueError,
        match=(
            "Unknown dataset profile 'unknown'; expected one of: "
            "artist_concept, character_concept, general"
        ),
    ):
        contracts.resolve_dataset_profile("unknown")


def test_core_profile_contract_imports_only_core_project_packages() -> None:
    contracts = _contracts()
    tree = ast.parse(Path(contracts.__file__).read_text(encoding="utf-8"))
    project_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            project_imports.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith("dataset_audit_studio.")
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("dataset_audit_studio.")
        ):
            project_imports.append(node.module)

    assert all(
        imported == "dataset_audit_studio.core"
        or imported.startswith("dataset_audit_studio.core.")
        for imported in project_imports
    )


def test_preset_does_not_reexport_the_canonical_core_profile_contract() -> None:
    preset_path = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "dataset_audit_studio"
        / "presets"
        / "builtin.py"
    )
    tree = ast.parse(preset_path.read_text(encoding="utf-8"), filename=str(preset_path))
    all_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    )
    exports = ast.literal_eval(all_assignment.value)
    forbidden = {
        "DatasetProfile",
        "ProfileConstraints",
        "PROFILE_CONSTRAINTS",
        "PROFILE_DEFAULT_DISABLED_COMPONENT_IDS",
        "PROFILE_OWNED_COMPONENT_IDS",
        "PROFILE_OWNED_CONFIG_FIELDS",
        "profile_constraints",
        "profile_owned_component_ids",
        "profile_owned_config_fields",
        "resolve_dataset_profile",
    }
    assert not forbidden.intersection(exports), (
        "presets.builtin must not re-export canonical core profile names: "
        f"{sorted(forbidden.intersection(exports))}"
    )
    presets = import_module("dataset_audit_studio.presets.builtin")
    assert not forbidden.intersection(vars(presets)), (
        "presets.builtin must not expose canonical core profile names: "
        f"{sorted(forbidden.intersection(vars(presets)))}"
    )


def test_preset_profile_specs_and_materialization_remain_stable() -> None:
    from dataset_audit_studio.app.profile_materialization import materialize_profile

    assert [item.id.value for item in PROFILE_SPECS] == [
        "artist_concept",
        "character_concept",
        "general",
    ]
    assert [(item.scope_mode, item.style_enabled) for item in PROFILE_SPECS] == [
        ("concept", True),
        ("concept", False),
        ("global", False),
    ]

    for profile in ("artist_concept", "character_concept", "general"):
        materialized = materialize_profile(profile)
        assert materialized["profile"] == profile
        assert "selection.three_stage" not in materialized["components"]
        semantic_enabled = True
        assert (
            materialized["components"]["embedding.semantic"]["enabled"]
            is semantic_enabled
        )
        assert (
            materialized["components"]["cluster.hierarchy"]["enabled"]
            is semantic_enabled
        )


def test_character_profile_owns_required_consistency_controls() -> None:
    contracts = _contracts()

    assert contracts.profile_owned_component_ids("artist_concept") == ("style.artist",)
    assert contracts.profile_owned_component_ids("general") == ("style.artist",)
    assert contracts.profile_owned_component_ids("character_concept") == (
        "style.artist",
        "embedding.semantic",
        "cluster.hierarchy",
    )
    assert dict(contracts.profile_owned_config_fields("character_concept")) == {
        "style.artist": ("enabled",),
        "cluster.hierarchy": ("scope_mode",),
    }


def test_builtin_profiles_share_calibrated_technical_metric_defaults() -> None:
    from dataset_audit_studio.app.profile_materialization import materialize_profile

    expected = {
        "minimum_rgb_entropy": 2.5,
        "maximum_black_ratio": 0.90,
        "maximum_white_ratio": 0.90,
        "minimum_laplacian_variance": 16.0,
        "maximum_high_frequency_ratio": 0.32,
        "maximum_border_ratio": 0.03,
        "maximum_blockiness": 0.35,
        "minimum_luminance_std": 10.0,
    }

    for profile in ("artist_concept", "character_concept", "general"):
        components = materialize_profile(profile)["components"]
        assert components["media.scan"]["config"]["thresholds"] == expected
