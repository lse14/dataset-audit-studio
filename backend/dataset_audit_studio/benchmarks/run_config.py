from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dataset_audit_studio.benchmarks.manifest import (
    BenchmarkManifest,
    _normalize_sha256,
)
from dataset_audit_studio.core.torch_runtime import DeviceRequest, Precision
from dataset_audit_studio.model_adapters.types import (
    MODEL_ID_PATTERN,
    REPOSITORY_PATTERN,
    REVISION_PATTERN,
)

RUN_CONFIG_SCHEMA_VERSION = "detector-benchmark-run/v1"
RUN_CONFIG_V2_SCHEMA_VERSION = "detector-benchmark-run/v2"
OCR_DETECTOR_MODEL_ID = "ppocrv5_server_det"
OCR_RECOGNIZER_MODEL_ID = "ppocrv5_server_rec"
OCR_MODEL_IDS = frozenset({OCR_DETECTOR_MODEL_ID, OCR_RECOGNIZER_MODEL_ID})


class BenchmarkArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    source_kind: Literal["github", "huggingface"]
    source_repository: str
    revision: str
    artifact_sha256: tuple[str, ...] = Field(min_length=1)
    declared_license: str = Field(min_length=1, max_length=80)
    remote_code_allowed: Literal[False]

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        if MODEL_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("model_id must be lowercase ASCII with underscores")
        return value

    @field_validator("source_repository")
    @classmethod
    def validate_source_repository(cls, value: str) -> str:
        if REPOSITORY_PATTERN.fullmatch(value) is None:
            raise ValueError("source_repository must use owner/name form")
        return value

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        normalized = value.casefold()
        if REVISION_PATTERN.fullmatch(normalized) is None:
            raise ValueError("revision must contain 40 lowercase hex characters")
        return normalized

    @field_validator("artifact_sha256")
    @classmethod
    def validate_artifact_sha256(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(
            _normalize_sha256(item, label="artifact_sha256") for item in value
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("artifact_sha256 values must be unique")
        return normalized


class BenchmarkModelReference(BenchmarkArtifactReference):
    role: Literal["baseline", "candidate"]


class BenchmarkModelReferenceV2(BenchmarkModelReference):
    batch_size: int = Field(gt=0)


class BenchmarkAuxiliaryModelReference(BenchmarkArtifactReference):
    batch_size: int = Field(gt=0)


class BenchmarkAuxiliaryOCRReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detector: BenchmarkAuxiliaryModelReference
    recognizer: BenchmarkAuxiliaryModelReference

    @model_validator(mode="after")
    def validate_model_roles(self) -> BenchmarkAuxiliaryOCRReference:
        if self.detector.model_id != OCR_DETECTOR_MODEL_ID:
            raise ValueError(f"auxiliary_ocr detector must be {OCR_DETECTOR_MODEL_ID}")
        if self.recognizer.model_id != OCR_RECOGNIZER_MODEL_ID:
            raise ValueError(f"auxiliary_ocr recognizer must be {OCR_RECOGNIZER_MODEL_ID}")
        return self


class BenchmarkRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[RUN_CONFIG_SCHEMA_VERSION]
    manifest_sha256: str
    seed: int = Field(ge=0)
    review_top_k: int = Field(gt=0)
    device: DeviceRequest
    precision: Precision
    offline: Literal[True]
    report_only: Literal[True]
    models: tuple[BenchmarkModelReference, ...] = Field(min_length=1)

    @field_validator("manifest_sha256")
    @classmethod
    def validate_manifest_sha256(cls, value: str) -> str:
        return _normalize_sha256(value, label="manifest_sha256")

    @model_validator(mode="after")
    def validate_unique_model_ids(self) -> BenchmarkRunConfig:
        model_ids = [model.model_id for model in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("run config model_id values must be unique")
        return self


class BenchmarkRunConfigV2(BenchmarkRunConfig):
    schema_version: Literal[RUN_CONFIG_V2_SCHEMA_VERSION]
    warmup_batches: int = Field(ge=0)
    timing_repeats: int = Field(gt=0)
    models: tuple[BenchmarkModelReferenceV2, ...] = Field(min_length=1)
    auxiliary_ocr: BenchmarkAuxiliaryOCRReference | None = None

    @model_validator(mode="after")
    def validate_ocr_is_auxiliary(self) -> BenchmarkRunConfigV2:
        if any(model.model_id in OCR_MODEL_IDS for model in self.models):
            raise ValueError("OCR model identities must be configured under auxiliary_ocr")
        return self

    @property
    def canonical_sha256(self) -> str:
        return canonical_run_config_sha256(self)


def canonical_run_config_sha256(config: BenchmarkRunConfigV2) -> str:
    encoded = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_run_config_manifest(
    config: BenchmarkRunConfig | BenchmarkRunConfigV2,
    manifest: BenchmarkManifest,
) -> None:
    if config.manifest_sha256 != manifest.canonical_sha256:
        raise ValueError("Run config manifest_sha256 does not match manifest canonical_sha256")
