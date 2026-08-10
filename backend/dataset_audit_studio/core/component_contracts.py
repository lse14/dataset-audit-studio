from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

ExecutionMode = Literal["cpu_inline", "cpu_process", "gpu_process"]
FailurePolicy = Literal["stop"]

_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*\.v[1-9][0-9]*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TASK_PHASE = re.compile(r"^[a-z][a-z0-9_]*$")


def _require_pattern(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not pattern.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")


@dataclass(frozen=True)
class CapabilityRequirement:
    capability: str
    optional: bool = False

    def __post_init__(self) -> None:
        _require_pattern(self.capability, _CAPABILITY, "capability")


@dataclass(frozen=True)
class CapabilityDeclaration:
    capability: str

    def __post_init__(self) -> None:
        _require_pattern(self.capability, _CAPABILITY, "capability")


@dataclass(frozen=True)
class ComponentManifest:
    id: str
    version: str
    phase_order: int
    config_schema: str
    task_phase: str = "worker"
    consumes: tuple[CapabilityRequirement, ...] = ()
    produces: tuple[CapabilityDeclaration, ...] = ()
    model_ids: tuple[str, ...] = ()
    execution: ExecutionMode = "cpu_process"
    failure_policy: FailurePolicy = "stop"
    default_enabled: bool = True

    def __post_init__(self) -> None:
        _require_pattern(self.id, _COMPONENT_ID, "component id")
        _require_pattern(self.version, _VERSION, "component version")
        if self.phase_order < 0:
            raise ValueError("Component phase_order cannot be negative")
        if not self.config_schema.strip():
            raise ValueError("Component config_schema cannot be empty")
        _require_pattern(self.task_phase, _TASK_PHASE, "task phase")
        if self.execution not in {"cpu_inline", "cpu_process", "gpu_process"}:
            raise ValueError(f"Unsupported component execution mode: {self.execution}")
        if self.failure_policy != "stop":
            raise ValueError("Enabled components must use the stop failure policy")
        capabilities = [item.capability for item in self.produces]
        if len(capabilities) != len(set(capabilities)):
            raise ValueError(f"Component {self.id} declares a capability more than once")
        if len(self.model_ids) != len(set(self.model_ids)):
            raise ValueError(f"Component {self.id} declares a model more than once")
        for model_id in self.model_ids:
            _require_pattern(model_id, _MODEL_ID, "model id")

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "phase_order": self.phase_order,
            "config_schema": self.config_schema,
            "task_phase": self.task_phase,
            "consumes": [
                {"capability": item.capability, "optional": item.optional}
                for item in self.consumes
            ],
            "produces": [item.capability for item in self.produces],
            "model_ids": list(self.model_ids),
            "execution": self.execution,
            "failure_policy": self.failure_policy,
            "default_enabled": self.default_enabled,
        }


class NormalizedComponentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_id: str
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("component_id")
    @classmethod
    def validate_component_id(cls, value: str) -> str:
        _require_pattern(value, _COMPONENT_ID, "component id")
        return value


class ArtifactIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    sample_id: str | None = None
    capability: str
    producer_id: str
    producer_version: str
    input_digest: str
    config_digest: str
    model_digest: str | None = None
    content_digest: str

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: str) -> str:
        _require_pattern(value, _CAPABILITY, "capability")
        return value

    @field_validator("producer_id")
    @classmethod
    def validate_producer_id(cls, value: str) -> str:
        _require_pattern(value, _COMPONENT_ID, "component id")
        return value

    @field_validator("producer_version")
    @classmethod
    def validate_producer_version(cls, value: str) -> str:
        _require_pattern(value, _VERSION, "component version")
        return value

    @field_validator(
        "input_digest",
        "config_digest",
        "model_digest",
        "content_digest",
    )
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        if value is not None:
            _require_pattern(value, _SHA256, "SHA-256 digest")
        return value


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ArtifactIdentity
    path: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class ComponentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    component_id: str
    worker_owner: str
    execution_epoch: int = Field(ge=0)
    normalized_config: dict[str, Any]
    component_order: tuple[str, ...] = ()
    input_artifacts: tuple[ArtifactReference, ...] = ()
    runtime_model_ids: tuple[str, ...] = ()
    checkpoint: dict[str, Any] = Field(default_factory=dict)

    @field_validator("component_id")
    @classmethod
    def validate_component_id(cls, value: str) -> str:
        _require_pattern(value, _COMPONENT_ID, "component id")
        return value


class ComponentBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_id: str
    batch_index: int = Field(ge=0)
    completed_items: int = Field(ge=0)
    component_complete: bool
    final_status: str
    artifacts: tuple[ArtifactReference, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    next_cursor: dict[str, Any] = Field(default_factory=dict)

    @field_validator("component_id")
    @classmethod
    def validate_component_id(cls, value: str) -> str:
        _require_pattern(value, _COMPONENT_ID, "component id")
        return value


class ComponentExecutionPort(Protocol):
    def execute(self, request: ComponentRunRequest) -> ComponentBatchResult: ...


ConfigNormalizer = Callable[[Mapping[str, Any]], NormalizedComponentConfig]
ModelResolver = Callable[[NormalizedComponentConfig], tuple[str, ...]]


@dataclass(frozen=True)
class ComponentDefinition:
    manifest: ComponentManifest
    normalize_config: ConfigNormalizer
    resolve_models: ModelResolver | None = None

    def model_ids(self, config: NormalizedComponentConfig) -> tuple[str, ...]:
        if self.resolve_models is not None:
            return self.resolve_models(config)
        return self.manifest.model_ids


@dataclass(frozen=True)
class ResolvedComponent:
    definition: ComponentDefinition
    config: NormalizedComponentConfig
    dependency_ids: tuple[str, ...]
    auto_enabled: bool
