from __future__ import annotations

import copy
import heapq
from collections.abc import Iterable, Mapping
from typing import Any

from dataset_audit_studio.core.component_contracts import (
    ComponentDefinition,
    NormalizedComponentConfig,
    ResolvedComponent,
)


class ComponentRegistryError(ValueError):
    pass


class ComponentRegistry:
    def __init__(
        self,
        definitions: Iterable[ComponentDefinition],
        *,
        external_capabilities: Iterable[str] = (),
    ) -> None:
        items = tuple(definitions)
        self._definitions = {item.manifest.id: item for item in items}
        if len(self._definitions) != len(items):
            raise ComponentRegistryError("Component ids must be unique")
        self._external = frozenset(external_capabilities)
        self._producers: dict[str, str] = {}
        for definition in items:
            for declaration in definition.manifest.produces:
                previous = self._producers.get(declaration.capability)
                if previous is not None:
                    raise ComponentRegistryError(
                        f"Capability {declaration.capability} has multiple producers: "
                        f"{previous}, {definition.manifest.id}"
                    )
                self._producers[declaration.capability] = definition.manifest.id
        self._validate_dependencies()
        self._topological_ids(frozenset(self._definitions))

    @property
    def definitions(self) -> tuple[ComponentDefinition, ...]:
        return tuple(
            sorted(
                self._definitions.values(),
                key=lambda item: (item.manifest.phase_order, item.manifest.id),
            )
        )

    def manifest_payloads(self) -> tuple[dict[str, Any], ...]:
        return tuple(item.manifest.public_dict() for item in self.definitions)

    def get(self, component_id: str) -> ComponentDefinition:
        try:
            return self._definitions[component_id]
        except KeyError as error:
            raise ComponentRegistryError(f"Unknown component: {component_id}") from error

    def normalize_task_config(
        self, task_config: Mapping[str, Any]
    ) -> dict[str, NormalizedComponentConfig]:
        if not isinstance(task_config, Mapping) or "components" not in task_config:
            raise ComponentRegistryError(
                "Task config must contain a complete components object"
            )
        return self._normalize_component_mapping(task_config["components"])

    def _normalize_component_mapping(
        self,
        raw_components: Any,
    ) -> dict[str, NormalizedComponentConfig]:
        if not isinstance(raw_components, Mapping):
            raise ComponentRegistryError("components config must be an object")
        expected = set(self._definitions)
        supplied = set(raw_components)
        unknown = sorted(supplied - expected)
        missing = sorted(expected - supplied)
        if unknown:
            raise ComponentRegistryError(f"Unknown component configs: {unknown}")
        if missing:
            raise ComponentRegistryError(f"Missing component configs: {missing}")

        normalized: dict[str, NormalizedComponentConfig] = {}
        for definition in self.definitions:
            component_id = definition.manifest.id
            entry = raw_components[component_id]
            if not isinstance(entry, Mapping):
                raise ComponentRegistryError(
                    f"Component config {component_id} must be an object"
                )
            extra = sorted(set(entry) - {"enabled", "config"})
            if extra:
                raise ComponentRegistryError(
                    f"Component config {component_id} has unknown keys: {extra}"
                )
            enabled = entry.get("enabled")
            config = entry.get("config")
            if not isinstance(enabled, bool):
                raise ComponentRegistryError(
                    f"Component config {component_id}.enabled must be boolean"
                )
            if not isinstance(config, Mapping):
                raise ComponentRegistryError(
                    f"Component config {component_id}.config must be an object"
                )
            normalized[component_id] = NormalizedComponentConfig(
                component_id=component_id,
                enabled=enabled,
                config=copy.deepcopy(dict(config)),
            )
        return normalized

    def resolve_task_config(
        self, task_config: Mapping[str, Any]
    ) -> tuple[ResolvedComponent, ...]:
        normalized = self.normalize_task_config(task_config)
        selected = {component_id for component_id, config in normalized.items() if config.enabled}
        pending = list(selected)
        while pending:
            component_id = pending.pop()
            definition = self._definitions[component_id]
            for requirement in definition.manifest.consumes:
                if requirement.optional or requirement.capability in self._external:
                    continue
                producer = self._producers[requirement.capability]
                if producer not in selected:
                    selected.add(producer)
                    pending.append(producer)

        ordered = self._topological_ids(frozenset(selected))
        resolved: list[ResolvedComponent] = []
        for component_id in ordered:
            definition = self._definitions[component_id]
            config = normalized[component_id]
            auto_enabled = not config.enabled
            if auto_enabled:
                config = config.model_copy(update={"enabled": True})
            dependencies = tuple(
                sorted(
                    {
                        self._producers[requirement.capability]
                        for requirement in definition.manifest.consumes
                        if requirement.capability in self._producers
                        and self._producers[requirement.capability] in selected
                    }
                )
            )
            resolved.append(
                ResolvedComponent(
                    definition=definition,
                    config=config,
                    dependency_ids=dependencies,
                    auto_enabled=auto_enabled,
                )
            )
        return tuple(resolved)

    def _validate_dependencies(self) -> None:
        for definition in self._definitions.values():
            for requirement in definition.manifest.consumes:
                if requirement.optional:
                    continue
                capability = requirement.capability
                if capability not in self._external and capability not in self._producers:
                    raise ComponentRegistryError(
                        f"Component {definition.manifest.id} requires missing capability "
                        f"{capability}"
                    )
                producer_id = self._producers.get(capability)
                if producer_id is None:
                    continue
                producer = self._definitions[producer_id]
                if producer.manifest.phase_order > definition.manifest.phase_order:
                    raise ComponentRegistryError(
                        f"Component {definition.manifest.id} consumes {capability} before "
                        "its producer"
                    )

    def _topological_ids(self, selected: frozenset[str]) -> tuple[str, ...]:
        dependencies: dict[str, set[str]] = {component_id: set() for component_id in selected}
        dependents: dict[str, set[str]] = {component_id: set() for component_id in selected}
        for component_id in selected:
            definition = self._definitions[component_id]
            for requirement in definition.manifest.consumes:
                producer = self._producers.get(requirement.capability)
                if producer is None or producer not in selected or producer == component_id:
                    continue
                dependencies[component_id].add(producer)
                dependents[producer].add(component_id)

        ready = [
            (self._definitions[item].manifest.phase_order, item)
            for item, values in dependencies.items()
            if not values
        ]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            _, component_id = heapq.heappop(ready)
            ordered.append(component_id)
            for dependent in sorted(dependents[component_id]):
                dependencies[dependent].discard(component_id)
                if not dependencies[dependent]:
                    heapq.heappush(
                        ready,
                        (self._definitions[dependent].manifest.phase_order, dependent),
                    )
        if len(ordered) != len(selected):
            unresolved = sorted(selected - set(ordered))
            raise ComponentRegistryError(f"Component dependency cycle: {unresolved}")
        return tuple(ordered)
