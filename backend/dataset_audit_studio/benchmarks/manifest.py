from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MANIFEST_SCHEMA_VERSION = "detector-benchmark-manifest/v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAMPLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")

Corpus = Literal["danbooru", "e621"]
Stratum = Literal[
    "human_anime",
    "ai_anime",
    "compressed_or_resized",
    "signature",
    "watermark",
    "ordinary_text",
    "no_watermark_hard_negative",
]
LabelTrust = Literal["trusted", "unknown"]


def _normalize_sha256(value: str, *, label: str) -> str:
    normalized = value.casefold()
    if SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            f"{label} must contain 64 hexadecimal characters and is normalized to lowercase"
        )
    return normalized


class LabelAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    label_source: str = Field(min_length=1, max_length=240)
    label_trust: LabelTrust

    @model_validator(mode="after")
    def validate_unknown_trust(self) -> LabelAnnotation:
        if (self.value == "unknown") != (self.label_trust == "unknown"):
            raise ValueError("unknown value requires label_trust=unknown and vice versa")
        return self


class OriginAnnotation(LabelAnnotation):
    value: Literal["human", "ai", "unknown"]


class PresenceAnnotation(LabelAnnotation):
    value: Literal["present", "absent", "unknown"]


def _require_trusted_value(
    annotation: LabelAnnotation,
    *,
    expected_value: str,
    stratum: str,
    label: str,
) -> None:
    if annotation.label_trust != "trusted" or annotation.value != expected_value:
        raise ValueError(f"{stratum} requires trusted {label}={expected_value}")


class WatermarkLabels(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    watermark: PresenceAnnotation
    signature: PresenceAnnotation
    logo: PresenceAnnotation
    artist_logo: PresenceAnnotation
    sample_watermark: PresenceAnnotation
    text: PresenceAnnotation


class DerivedTransform(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_image_sha256: str
    kind: Literal["compression", "resize"]
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parent_image_sha256")
    @classmethod
    def validate_parent_image_sha256(cls, value: str) -> str:
        return _normalize_sha256(value, label="parent_image_sha256")

    @field_validator("parameters")
    @classmethod
    def validate_parameters_are_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("derivation parameters must be JSON serializable") from error
        return value


class BenchmarkManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[MANIFEST_SCHEMA_VERSION]
    sample_id: str
    image_path: str
    image_sha256: str
    source_corpus: Corpus
    strata: tuple[Stratum, ...] = Field(min_length=1)
    derivation: DerivedTransform | None = None
    ai_origin: OriginAnnotation
    watermark_labels: WatermarkLabels

    @field_validator("sample_id")
    @classmethod
    def validate_sample_id(cls, value: str) -> str:
        if SAMPLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("sample_id must be a stable ASCII identifier")
        return value

    @field_validator("image_path")
    @classmethod
    def validate_image_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("image_path must be a safe relative path using forward slashes")
        path = PurePosixPath(value)
        if not value or path.is_absolute() or "." in path.parts or ".." in path.parts:
            raise ValueError("image_path must be a safe relative path")
        return path.as_posix()

    @field_validator("image_sha256")
    @classmethod
    def validate_image_sha256(cls, value: str) -> str:
        return _normalize_sha256(value, label="image_sha256")

    @field_validator("strata")
    @classmethod
    def validate_unique_strata(cls, value: tuple[Stratum, ...]) -> tuple[Stratum, ...]:
        if len(value) != len(set(value)):
            raise ValueError("strata must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_derivation(self) -> BenchmarkManifestEntry:
        is_derived = "compressed_or_resized" in self.strata
        if is_derived and self.derivation is None:
            raise ValueError("compressed_or_resized requires derivation")
        if not is_derived and self.derivation is not None:
            raise ValueError("derivation requires compressed_or_resized stratum")
        if (
            self.derivation is not None
            and self.image_sha256 == self.derivation.parent_image_sha256
        ):
            raise ValueError("derived image_sha256 must differ from parent_image_sha256")
        return self

    @model_validator(mode="after")
    def validate_strata_annotations(self) -> BenchmarkManifestEntry:
        strata = set(self.strata)
        if {"human_anime", "ai_anime"}.issubset(strata):
            raise ValueError("human_anime and ai_anime are mutually exclusive")
        if "human_anime" in strata:
            _require_trusted_value(
                self.ai_origin,
                expected_value="human",
                stratum="human_anime",
                label="ai_origin",
            )
        if "ai_anime" in strata:
            _require_trusted_value(
                self.ai_origin,
                expected_value="ai",
                stratum="ai_anime",
                label="ai_origin",
            )
        if "signature" in strata:
            _require_trusted_value(
                self.watermark_labels.signature,
                expected_value="present",
                stratum="signature",
                label="signature",
            )
        if "watermark" in strata:
            _require_trusted_value(
                self.watermark_labels.watermark,
                expected_value="present",
                stratum="watermark",
                label="watermark",
            )
        if "ordinary_text" in strata:
            _require_trusted_value(
                self.watermark_labels.text,
                expected_value="present",
                stratum="ordinary_text",
                label="text",
            )
        if "no_watermark_hard_negative" in strata:
            _require_trusted_value(
                self.watermark_labels.watermark,
                expected_value="absent",
                stratum="no_watermark_hard_negative",
                label="watermark",
            )
        return self


class BenchmarkManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[BenchmarkManifestEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_sample_ids(self) -> BenchmarkManifest:
        sample_ids = [entry.sample_id for entry in self.entries]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("manifest sample_id values must be unique")
        image_hashes = [entry.image_sha256 for entry in self.entries]
        if len(image_hashes) != len(set(image_hashes)):
            raise ValueError("manifest image_sha256 values must be unique")
        return self

    @property
    def canonical_sha256(self) -> str:
        return canonical_manifest_sha256(self.entries)


def load_manifest(path: Path) -> BenchmarkManifest:
    entries: list[BenchmarkManifestEntry] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"Manifest line {line_number} must not be blank")
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Manifest line {line_number} is not valid JSON") from error
        if not isinstance(raw, dict):
            raise ValueError(f"Manifest line {line_number} must contain an object")
        try:
            entries.append(BenchmarkManifestEntry.model_validate(raw))
        except ValidationError as error:
            raise ValueError(f"Manifest line {line_number} is invalid: {error}") from error
    return BenchmarkManifest(entries=tuple(entries))


def canonical_manifest_sha256(entries: Iterable[BenchmarkManifestEntry]) -> str:
    encoded_entries = (
        json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for entry in sorted(entries, key=lambda item: item.sample_id)
    )
    return hashlib.sha256(b"".join(encoded_entries)).hexdigest()
