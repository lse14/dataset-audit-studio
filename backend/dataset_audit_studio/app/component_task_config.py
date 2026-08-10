from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from dataset_audit_studio.app.component_catalog import COMPONENT_REGISTRY
from dataset_audit_studio.app.component_registration import (
    BUILTIN_COMPONENT_REGISTRATION_CATALOG,
)
from dataset_audit_studio.core.component_registry import ComponentRegistry
from dataset_audit_studio.core.profile_contracts import (
    DatasetProfile,
    resolve_dataset_profile,
)
from dataset_audit_studio.jobs.errors import LegacyTaskConfigUnsupported
from dataset_audit_studio.presets.builtin import (
    apply_profile,
    profile_from_components,
)


class ComponentTaskConfigMaterializer:
    def __init__(self, registry: ComponentRegistry = COMPONENT_REGISTRY) -> None:
        self.registry = registry

    def materialize(
        self,
        raw_components: Mapping[str, Any],
        *,
        profile: DatasetProfile | str | None = None,
        require_profile: bool = False,
    ) -> dict[str, Any]:
        expected = {item.manifest.id for item in self.registry.definitions}
        supplied = set(raw_components)
        unknown = sorted(supplied - expected)
        missing = sorted(expected - supplied)
        if unknown:
            raise ValueError(f"Unknown component configs: {unknown}")
        if missing:
            if require_profile:
                raise LegacyTaskConfigUnsupported(
                    "legacy_task_config_unsupported: complete components are required"
                )
            raise ValueError(f"Missing component configs: {missing}")

        resolved_profile = resolve_dataset_profile(profile) if profile is not None else None
        raw_components = self._apply_builtin_profile(
            raw_components,
            profile=resolved_profile,
            require_profile=require_profile,
        )

        components: dict[str, dict[str, Any]] = {}
        for definition in self.registry.definitions:
            component_id = definition.manifest.id
            raw = raw_components[component_id]
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump(mode="python")
            if not isinstance(raw, Mapping):
                raise TypeError(f"Component config {component_id} must be an object")
            extra = sorted(set(raw) - {"enabled", "config"})
            if extra:
                raise ValueError(
                    f"Component config {component_id} has unknown keys: {extra}"
                )
            enabled = raw.get("enabled")
            if not isinstance(enabled, bool):
                raise TypeError(f"Component config {component_id}.enabled must be boolean")
            registration = BUILTIN_COMPONENT_REGISTRATION_CATALOG.registration_for(
                component_id
            )
            ui = registration.ui_contract
            if ui.activation == "required" and not enabled:
                raise ValueError(f"Required component cannot be disabled: {component_id}")
            if ui.activation == "auto" and enabled:
                raise ValueError(f"Auto component cannot be enabled directly: {component_id}")
            raw_config = raw.get("config")
            if not isinstance(raw_config, Mapping):
                raise TypeError(f"Component config {component_id}.config must be an object")
            model = registration.config_model
            validated = model.model_validate(dict(raw_config)).model_dump(mode="json")
            if "enabled" in model.model_fields:
                validated["enabled"] = enabled
            components[component_id] = {"enabled": enabled, "config": validated}

        if self._config(components, "export.dataset")["mode"] == "copy":
            components["latent.resolve"]["enabled"] = False

        normalized_container = {"components": components}
        resolved = self.registry.resolve_task_config(normalized_container)
        effective_ids = {item.definition.manifest.id for item in resolved}
        if resolved_profile is None:
            resolved_profile = profile_from_components(raw_components, require_profile=False)
        if require_profile and resolved_profile is None:
            raise LegacyTaskConfigUnsupported(
                "legacy_task_config_unsupported: profile is required"
            )
        return {
            "profile": resolved_profile.value if resolved_profile is not None else None,
            "components": copy.deepcopy(components),
            "scan": copy.deepcopy(self._config(components, "media.scan")),
            "scoring": self._scoring_config(components, effective_ids),
            "style": self._style_config(components, effective_ids),
            "clustering": self._clustering_config(
                components,
                effective_ids,
                profile=resolved_profile,
            ),
            "latent": self._latent_config(components, effective_ids),
            "export": copy.deepcopy(self._config(components, "export.dataset")),
        }

    def materialize_task_config(
        self,
        task_config: Mapping[str, Any],
        *,
        profile: DatasetProfile | str | None = None,
        require_profile: bool = False,
    ) -> dict[str, Any]:
        if "components" not in task_config:
            raise LegacyTaskConfigUnsupported(
                "legacy_task_config_unsupported: task config requires components"
            )
        raw_components = task_config["components"]
        if not isinstance(raw_components, Mapping):
            raise LegacyTaskConfigUnsupported(
                "legacy_task_config_unsupported: components must be an object"
            )

        components = copy.deepcopy(dict(raw_components))
        selected_profile = (
            resolve_dataset_profile(profile)
            if profile is not None
            else profile_from_components(task_config, require_profile=False)
        )
        return self.materialize(
            components,
            profile=selected_profile,
            require_profile=require_profile or selected_profile is not None,
        )

    @staticmethod
    def profile_from_task_config(
        task_config: Mapping[str, Any],
    ) -> DatasetProfile | None:
        if "components" not in task_config:
            return None
        components = task_config["components"]
        if not isinstance(components, Mapping):
            raise TypeError("Component config container must be an object")
        return profile_from_components(task_config, require_profile=False)

    def components_from_task_config(
        self,
        task_config: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        normalized = self.registry.normalize_task_config(task_config)
        components: dict[str, dict[str, Any]] = {}
        for definition in self.registry.definitions:
            component_id = definition.manifest.id
            value = normalized[component_id]
            ui = BUILTIN_COMPONENT_REGISTRATION_CATALOG.registration_for(
                component_id
            ).ui_contract
            enabled = value.enabled
            if ui.activation == "required":
                enabled = True
            elif ui.activation == "auto":
                enabled = False
            components[component_id] = {
                "enabled": enabled,
                "config": copy.deepcopy(value.config),
            }
        profile = profile_from_components(task_config, require_profile=True)
        return copy.deepcopy(
            self.materialize(components, profile=profile, require_profile=True)["components"]
        )

    @staticmethod
    def _config(components: Mapping[str, Any], component_id: str) -> dict[str, Any]:
        return components[component_id]["config"]

    @staticmethod
    def _apply_builtin_profile(
        raw_components: Mapping[str, Any],
        *,
        profile: DatasetProfile | None,
        require_profile: bool,
    ) -> dict[str, Any]:
        copied: dict[str, Any] = {}
        for component_id, raw in raw_components.items():
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump(mode="python")
            copied[component_id] = copy.deepcopy(raw)
        try:
            selected = profile or profile_from_components(copied, require_profile=require_profile)
        except ValueError as error:
            if require_profile and "require a dataset profile" in str(error):
                raise LegacyTaskConfigUnsupported(
                    "legacy_task_config_unsupported: profile is required"
                ) from error
            raise
        return apply_profile(copied, selected) if selected is not None else copied

    def _scoring_config(
        self,
        components: Mapping[str, Any],
        effective_ids: set[str],
    ) -> dict[str, Any]:
        scoring_ids = (
            "score.aesthetic_domain",
            "detect.ai",
            "evidence.ocr",
            "evidence.watermark",
        )
        enabled = [component_id for component_id in scoring_ids if component_id in effective_ids]
        runtime_ids = [
            component_id
            for component_id in ("feature.clip_l14", *enabled)
            if component_id in effective_ids
        ]
        runtime_values = {
            (
                self._config(components, component_id).get("device", "auto"),
                self._config(components, component_id).get("precision", "float32"),
            )
            for component_id in runtime_ids
        }
        if len(runtime_values) > 1:
            raise ValueError(
                "Enabled scoring components must use the same device and precision"
            )
        device, precision = next(iter(runtime_values), ("auto", "float32"))
        clip = self._config(components, "feature.clip_l14")
        aesthetic = self._config(components, "score.aesthetic_domain")
        ai = self._config(components, "detect.ai")
        ocr = self._config(components, "evidence.ocr")
        watermark = self._config(components, "evidence.watermark")
        return {
            "enabled": bool(enabled),
            "device": device,
            "precision": precision,
            "batch_size": clip["batch_size"],
            "aesthetic": {
                "enabled": "score.aesthetic_domain" in effective_ids,
                "model_id": aesthetic["model_id"],
                "in_domain_threshold": aesthetic["in_domain_threshold"],
                "jtp_max_sequence": aesthetic["jtp_max_sequence"],
            },
            "ai": {
                "enabled": "detect.ai" in effective_ids,
                "model_id": ai["model_id"],
                "candidate_threshold": ai["candidate_threshold"],
                "reference_threshold": ai["reference_threshold"],
            },
            "ocr": {
                "enabled": "evidence.ocr" in effective_ids,
                "bitmap_threshold": ocr["bitmap_threshold"],
                "box_threshold": ocr["box_threshold"],
                "unclip_ratio": ocr["unclip_ratio"],
                "min_size": ocr["min_size"],
                "max_candidates": ocr["max_candidates"],
                "recognition_batch_size": ocr["recognition_batch_size"],
                "text_density_threshold": ocr["text_density_threshold"],
            },
            "watermark": {
                "enabled": "evidence.watermark" in effective_ids,
                "review_threshold": watermark["review_threshold"],
            },
        }

    def _style_config(
        self,
        components: Mapping[str, Any],
        effective_ids: set[str],
    ) -> dict[str, Any]:
        config = copy.deepcopy(self._config(components, "style.artist"))
        config["enabled"] = "style.artist" in effective_ids
        return config

    def _clustering_config(
        self,
        components: Mapping[str, Any],
        effective_ids: set[str],
        *,
        profile: DatasetProfile | None,
    ) -> dict[str, Any]:
        semantic = self._config(components, "embedding.semantic")
        hierarchy = self._config(components, "cluster.hierarchy")
        sae = copy.deepcopy(self._config(components, "analysis.sae"))
        sae["enabled"] = "analysis.sae" in effective_ids
        scope_mode = hierarchy["scope_mode"]
        if profile == DatasetProfile.ARTIST_CONCEPT and scope_mode == "concept":
            scope_mode = "artist"
        return {
            "enabled": "embedding.semantic" in effective_ids,
            "scope_mode": scope_mode,
            "device": semantic["device"],
            "embedding_batch_size": semantic["batch_size"],
            "embedding_shard_size": semantic["shard_size"],
            "minimum_split_size": hierarchy["minimum_split_size"],
            "target_leaf_size": hierarchy["target_leaf_size"],
            "max_branching": hierarchy["max_branching"],
            "kmeans_iterations": hierarchy["kmeans_iterations"],
            "seed": hierarchy["seed"],
            "phash_max_distance": 4,
            "colorhash_max_distance": 2,
            "semantic_duplicate_threshold": 0.985,
            "sae": sae,
        }

    def _latent_config(
        self,
        components: Mapping[str, Any],
        effective_ids: set[str],
    ) -> dict[str, Any]:
        config = copy.deepcopy(self._config(components, "latent.resolve"))
        if "latent.resolve" not in effective_ids:
            config["mikazuki_enabled"] = False
            config["single_file_rules"] = []
        return config
