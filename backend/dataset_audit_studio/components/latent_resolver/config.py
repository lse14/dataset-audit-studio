from __future__ import annotations

from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_SINGLE_LATENT_SUFFIXES = frozenset({".npz", ".safetensors"})


class SingleLatentRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    pattern: str = Field(min_length=1, max_length=255)
    cache_kind: str = Field(default="single_file", pattern=r"^[a-z][a-z0-9_-]*$")

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, value: str) -> str:
        if PurePath(value).name != value or "/" in value or "\\" in value:
            raise ValueError("Single latent pattern must be a file name in the image directory")
        if value.count("{stem}") + value.count("{name}") != 1:
            raise ValueError("Single latent pattern requires exactly one {stem} or {name} token")
        remainder = value.replace("{stem}", "sample").replace("{name}", "sample.png")
        if "{" in remainder or "}" in remainder:
            raise ValueError("Single latent pattern contains an unsupported token")
        if PurePath(remainder).suffix.casefold() not in SUPPORTED_SINGLE_LATENT_SUFFIXES:
            raise ValueError("Single latent pattern must produce .npz or .safetensors")
        return value


class LatentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mikazuki_enabled: bool = True
    mikazuki_namespaces: tuple[str, ...] = ("anima",)
    single_file_rules: tuple[SingleLatentRule, ...] = ()

    @field_validator("mikazuki_namespaces")
    @classmethod
    def normalize_namespaces(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values if value.strip())
        if len(cleaned) > 16:
            raise ValueError("At most sixteen Mikazuki namespaces are supported")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("Mikazuki namespaces must be unique")
        if any(
            not value.replace("_", "").replace("-", "").isalnum()
            for value in cleaned
        ):
            raise ValueError("Mikazuki namespaces contain unsafe characters")
        return cleaned

    @classmethod
    def from_task_config(cls, config: dict[str, Any]) -> LatentConfig:
        candidate = config.get("latent", {})
        if not isinstance(candidate, dict):
            raise ValueError("latent config must be an object")
        return cls.model_validate(candidate)
