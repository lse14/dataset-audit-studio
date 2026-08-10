"""Versioned, in-memory validation contracts for detector benchmark sidecars.

This module deliberately does not run adapters or publish files. The harness in a
later roadmap item consumes these contracts after it has completed its own model
lifecycle and timing work.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeAlias

from PIL import Image
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    TypeAdapter,
    field_validator,
    model_validator,
)

from dataset_audit_studio.benchmarks.detector_preflight import (
    REQUIRED_WD14_TAGS,
    DetectorPreflightReport,
)
from dataset_audit_studio.benchmarks.manifest import (
    SAMPLE_ID_PATTERN,
    BenchmarkManifest,
    BenchmarkManifestEntry,
    _normalize_sha256,
)
from dataset_audit_studio.benchmarks.run_config import (
    OCR_DETECTOR_MODEL_ID,
    OCR_RECOGNIZER_MODEL_ID,
    RUN_CONFIG_V2_SCHEMA_VERSION,
    BenchmarkRunConfigV2,
    validate_run_config_manifest,
)
from dataset_audit_studio.core.torch_runtime import DeviceRequest, Precision

RUN_SCHEMA_VERSION = "detector-benchmark-sidecar-run/v1"
SCORE_SCHEMA_VERSION = "detector-benchmark-sidecar-score/v1"
BATCH_SCHEMA_VERSION = "detector-benchmark-sidecar-batch/v1"
OCR_SCHEMA_VERSION = "detector-benchmark-sidecar-ocr/v1"
FAILURE_SCHEMA_VERSION = "detector-benchmark-sidecar-failure/v1"

DetectorModelId = Literal[
    "universal_fake_detector_head",
    "commfor_model_384",
    "watermark_siglip2",
    "wd14_eva02_large_v3",
]
SUPPORTED_DETECTOR_MODEL_IDS: tuple[DetectorModelId, ...] = (
    "universal_fake_detector_head",
    "commfor_model_384",
    "watermark_siglip2",
    "wd14_eva02_large_v3",
)
WD14_TAG_SCORE_LABELS = tuple(sorted(REQUIRED_WD14_TAGS))


def _validate_sample_id(value: str, *, label: str) -> str:
    if SAMPLE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a stable ASCII identifier")
    return value


def _validate_safe_relative_path(value: str, *, label: str) -> str:
    if "\\" in value:
        raise ValueError(f"{label} must use forward slashes")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ValueError(f"{label} must be a safe relative path")
    return path.as_posix()


class BenchmarkSidecarAssetFile(BaseModel):
    """A pinned file that contributed to one detector or dependency asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    size_bytes: int = Field(gt=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_safe_relative_path(value, label="asset file path")

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _normalize_sha256(value, label="asset file SHA-256")


class BenchmarkSidecarAsset(BaseModel):
    """The model or dependency asset set used by one detector pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=160)
    role: Literal["model", "dependency"]
    files: tuple[BenchmarkSidecarAssetFile, ...] = Field(min_length=1)

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("asset_id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_unique_files(self) -> BenchmarkSidecarAsset:
        paths = [file.path for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("asset file paths must be unique")
        return self


class BenchmarkSidecarPreprocessingSource(BaseModel):
    """The fixed preprocessing implementation identity for one detector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, max_length=240)
    revision: str = Field(min_length=1, max_length=160)


class BenchmarkSidecarDetectorProvenance(BaseModel):
    """Assets and preprocessing provenance for a detector in ``run.json``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: DetectorModelId
    assets: tuple[BenchmarkSidecarAsset, ...] = Field(min_length=1)
    preprocessing: BenchmarkSidecarPreprocessingSource

    @model_validator(mode="after")
    def validate_unique_assets(self) -> BenchmarkSidecarDetectorProvenance:
        asset_keys = [(asset.asset_id, asset.role) for asset in self.assets]
        if len(asset_keys) != len(set(asset_keys)):
            raise ValueError("detector provenance assets must be unique")
        if not any(asset.role == "model" for asset in self.assets):
            raise ValueError("detector provenance requires one model asset")
        return self


class BenchmarkSidecarAuxiliaryOcrProvenance(BaseModel):
    """Pinned assets and preprocessing identity for independent OCR evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assets: tuple[BenchmarkSidecarAsset, BenchmarkSidecarAsset]
    preprocessing: BenchmarkSidecarPreprocessingSource

    @model_validator(mode="after")
    def validate_assets(self) -> BenchmarkSidecarAuxiliaryOcrProvenance:
        observed = tuple((asset.asset_id, asset.role) for asset in self.assets)
        expected = (
            (OCR_DETECTOR_MODEL_ID, "model"),
            (OCR_RECOGNIZER_MODEL_ID, "model"),
        )
        if observed != expected:
            raise ValueError("OCR provenance must contain detector and recognizer model assets")
        return self


class BenchmarkCudaMemoryMeasurement(BaseModel):
    """CUDA allocated/reserved baselines and peaks for one timed batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_allocated_bytes: int = Field(ge=0)
    baseline_reserved_bytes: int = Field(ge=0)
    peak_allocated_bytes: int = Field(ge=0)
    peak_reserved_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_peaks(self) -> BenchmarkCudaMemoryMeasurement:
        if self.peak_allocated_bytes < self.baseline_allocated_bytes:
            raise ValueError("peak allocated memory must not be below its baseline")
        if self.peak_reserved_bytes < self.baseline_reserved_bytes:
            raise ValueError("peak reserved memory must not be below its baseline")
        return self


class BenchmarkSidecarRun(BaseModel):
    """The provenance-only contract for ``run.json``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[RUN_SCHEMA_VERSION]
    run_config_schema_version: Literal[RUN_CONFIG_V2_SCHEMA_VERSION]
    run_config_sha256: str
    manifest_sha256: str
    canonical_score_sha256: str
    detector_model_ids: tuple[DetectorModelId, ...] = Field(min_length=1)
    detector_provenance: tuple[BenchmarkSidecarDetectorProvenance, ...] = Field(
        min_length=1
    )
    auxiliary_ocr_enabled: bool
    auxiliary_ocr_provenance: BenchmarkSidecarAuxiliaryOcrProvenance | None = None
    report_only: Literal[True]
    requested_device: DeviceRequest
    actual_device: str = Field(min_length=1, max_length=160)
    requested_precision: Precision
    actual_precision: Precision
    software: dict[str, str] = Field(min_length=1)
    hardware: dict[str, str] = Field(min_length=1)

    @field_validator("run_config_sha256", "manifest_sha256", "canonical_score_sha256")
    @classmethod
    def validate_hashes(cls, value: str, info: Any) -> str:
        return _normalize_sha256(value, label=info.field_name)

    @field_validator("detector_model_ids")
    @classmethod
    def validate_unique_models(
        cls,
        value: tuple[DetectorModelId, ...],
    ) -> tuple[DetectorModelId, ...]:
        if len(value) != len(set(value)):
            raise ValueError("detector_model_ids must be unique")
        return value

    @field_validator("software", "hardware")
    @classmethod
    def validate_environment_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not item.strip() for key, item in value.items()):
            raise ValueError("environment fields must use nonblank keys and values")
        return value


class BenchmarkSidecarBatch(BaseModel):
    """The provenance-only contract for one ``batches.jsonl`` row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[BATCH_SCHEMA_VERSION]
    batch_id: str
    model_id: DetectorModelId
    sample_ids: tuple[str, ...] = Field(min_length=1)
    repeat: int = Field(ge=0)
    batch_size: int = Field(gt=0)
    end_to_end_duration_ms: FiniteFloat = Field(ge=0)
    memory_measurement_supported: bool
    cuda_memory: BenchmarkCudaMemoryMeasurement | None

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        return _validate_sample_id(value, label="batch_id")

    @field_validator("sample_ids")
    @classmethod
    def validate_sample_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for sample_id in value:
            _validate_sample_id(sample_id, label="sample_id")
        if len(value) != len(set(value)):
            raise ValueError("batch sample_ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_measurement_shape(self) -> BenchmarkSidecarBatch:
        if self.batch_size != len(self.sample_ids):
            raise ValueError("batch_size must match the number of sample_ids")
        if self.memory_measurement_supported != (self.cuda_memory is not None):
            raise ValueError(
                "cuda_memory must be present exactly when memory measurement is supported"
            )
        return self


class _BenchmarkScoreRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCORE_SCHEMA_VERSION]
    batch_id: str
    sample_id: str

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        return _validate_sample_id(value, label="batch_id")

    @field_validator("sample_id")
    @classmethod
    def validate_sample_id(cls, value: str) -> str:
        return _validate_sample_id(value, label="sample_id")


class UniversalFakeDetectScore(_BenchmarkScoreRecord):
    model_id: Literal["universal_fake_detector_head"]
    raw_sigmoid_score: FiniteFloat


class CommunityForensicsScore(_BenchmarkScoreRecord):
    model_id: Literal["commfor_model_384"]
    raw_sigmoid_score: FiniteFloat


class WatermarkSiglip2Score(_BenchmarkScoreRecord):
    model_id: Literal["watermark_siglip2"]
    raw_softmax_label_score: FiniteFloat
    raw_softmax_label_scores: dict[str, FiniteFloat] = Field(min_length=1)

    @field_validator("raw_softmax_label_scores")
    @classmethod
    def validate_label_score_keys(cls, value: dict[str, FiniteFloat]) -> dict[str, FiniteFloat]:
        if any(not label.strip() for label in value):
            raise ValueError("raw_softmax_label_scores must use nonempty labels")
        return value


class WD14TaggerScore(_BenchmarkScoreRecord):
    model_id: Literal["wd14_eva02_large_v3"]
    raw_sigmoid_tag_scores: dict[str, FiniteFloat]

    @field_validator("raw_sigmoid_tag_scores")
    @classmethod
    def validate_approved_tag_scores(
        cls,
        value: dict[str, FiniteFloat],
    ) -> dict[str, FiniteFloat]:
        observed = set(value)
        approved = set(WD14_TAG_SCORE_LABELS)
        if observed != approved:
            missing = sorted(approved - observed)
            extra = sorted(observed - approved)
            raise ValueError(
                "raw_sigmoid_tag_scores must contain exactly the approved WD14 labels "
                f"(missing={missing}, extra={extra})"
            )
        return value


DetectorScoreRecord: TypeAlias = (
    UniversalFakeDetectScore
    | CommunityForensicsScore
    | WatermarkSiglip2Score
    | WD14TaggerScore
)
_DETECTOR_SCORE_ADAPTER = TypeAdapter(DetectorScoreRecord)
_SCORE_RECORD_TYPES: dict[DetectorModelId, type[_BenchmarkScoreRecord]] = {
    "universal_fake_detector_head": UniversalFakeDetectScore,
    "commfor_model_384": CommunityForensicsScore,
    "watermark_siglip2": WatermarkSiglip2Score,
    "wd14_eva02_large_v3": WD14TaggerScore,
}
_RAW_OUTPUT_KEYS: dict[DetectorModelId, frozenset[str]] = {
    "universal_fake_detector_head": frozenset({"raw_sigmoid_score"}),
    "commfor_model_384": frozenset({"raw_sigmoid_score"}),
    "watermark_siglip2": frozenset(
        {"raw_softmax_label_score", "raw_softmax_label_scores"}
    ),
    "wd14_eva02_large_v3": frozenset({"raw_sigmoid_tag_scores"}),
}


class BenchmarkOcrRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    box: tuple[tuple[FiniteFloat, FiniteFloat], ...] = Field(min_length=4, max_length=4)
    detection_score: FiniteFloat
    recognition_score: FiniteFloat
    text: str


class BenchmarkOcrRecord(BaseModel):
    """The independent optional ``ocr.jsonl`` record contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[OCR_SCHEMA_VERSION]
    sample_id: str
    detector_model_id: Literal[OCR_DETECTOR_MODEL_ID]
    recognizer_model_id: Literal[OCR_RECOGNIZER_MODEL_ID]
    regions: tuple[BenchmarkOcrRegion, ...]
    text_area_ratio: FiniteFloat

    @field_validator("sample_id")
    @classmethod
    def validate_sample_id(cls, value: str) -> str:
        return _validate_sample_id(value, label="sample_id")


class BenchmarkSidecarFailure(BaseModel):
    """The standalone, non-partial ``failure.json`` contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[FAILURE_SCHEMA_VERSION]
    stage: Literal[
        "preflight",
        "input_validation",
        "adapter_load",
        "output_validation",
        "execution",
        "publication",
    ]
    message: str = Field(min_length=1, max_length=4_000)
    model_id: (
        DetectorModelId | Literal[OCR_DETECTOR_MODEL_ID, OCR_RECOGNIZER_MODEL_ID] | None
    ) = None
    batch_id: str | None = None
    exception_type: str = Field(min_length=1, max_length=240)
    completed_score_count: int = Field(ge=0)
    completed_batch_count: int = Field(ge=0)

    @field_validator("batch_id")
    @classmethod
    def validate_optional_batch_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_sample_id(value, label="batch_id")


@dataclass(frozen=True)
class ValidatedBenchmarkInput:
    entry: BenchmarkManifestEntry
    path: Path


def validate_benchmark_inputs(
    *,
    manifest: BenchmarkManifest,
    run_config: BenchmarkRunConfigV2,
    preflight_reports: Sequence[DetectorPreflightReport],
    images_root: Path,
) -> tuple[ValidatedBenchmarkInput, ...]:
    """Validate the complete immutable input set before any adapter can load."""
    validate_run_config_manifest(run_config, manifest)
    validate_benchmark_preflight(
        run_config=run_config,
        preflight_reports=preflight_reports,
    )

    root = Path(images_root)
    if not root.exists() or not root.is_dir():
        raise ValueError("images_root must be an existing directory")
    resolved_root = root.resolve(strict=True)
    validated: list[ValidatedBenchmarkInput] = []
    for entry in sorted(manifest.entries, key=lambda item: item.sample_id):
        path = _safe_image_path(resolved_root, entry)
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != entry.image_sha256:
            raise ValueError(f"image SHA-256 mismatch for sample_id={entry.sample_id}")
        _validate_image_decode(path, entry.sample_id)
        validated.append(ValidatedBenchmarkInput(entry=entry, path=path))
    return tuple(validated)


def validate_benchmark_preflight(
    *,
    run_config: BenchmarkRunConfigV2,
    preflight_reports: Sequence[DetectorPreflightReport],
) -> None:
    """Require every configured detector/OCR preflight before adapter construction."""
    _validate_supported_detector_models(run_config)
    _validate_preflight_reports(run_config, preflight_reports)


def validate_detector_batch_outputs(
    *,
    model_id: str,
    batch_id: str,
    sample_ids: Sequence[str],
    outputs: Sequence[Mapping[str, Any]],
) -> tuple[DetectorScoreRecord, ...]:
    """Validate one adapter batch and convert it to score-sidecar rows."""
    if model_id not in _SCORE_RECORD_TYPES:
        raise ValueError(f"unsupported detector output model_id: {model_id}")
    detector_model_id = model_id
    normalized_sample_ids = tuple(sample_ids)
    if not normalized_sample_ids:
        raise ValueError("detector batch must contain at least one sample")
    if normalized_sample_ids != tuple(sorted(normalized_sample_ids)):
        raise ValueError("detector batch sample_ids must use stable sorted order")
    if len(normalized_sample_ids) != len(set(normalized_sample_ids)):
        raise ValueError("detector batch sample_ids must be unique")
    for sample_id in normalized_sample_ids:
        _validate_sample_id(sample_id, label="sample_id")
    _validate_sample_id(batch_id, label="batch_id")
    if len(outputs) != len(normalized_sample_ids):
        raise ValueError("detector output count does not match batch sample count")

    expected_keys = _RAW_OUTPUT_KEYS[detector_model_id]
    record_type = _SCORE_RECORD_TYPES[detector_model_id]
    records: list[DetectorScoreRecord] = []
    for sample_id, raw_output in zip(normalized_sample_ids, outputs, strict=True):
        if not isinstance(raw_output, Mapping):
            raise ValueError("detector output must be an object")
        if set(raw_output) != expected_keys:
            raise ValueError(
                f"{detector_model_id} output keys must be exactly {sorted(expected_keys)}"
            )
        record = record_type.model_validate(
            {
                "schema_version": SCORE_SCHEMA_VERSION,
                "batch_id": batch_id,
                "sample_id": sample_id,
                "model_id": detector_model_id,
                **raw_output,
            }
        )
        records.append(record)
    return tuple(records)


def validate_ocr_batch_outputs(
    *,
    sample_ids: Sequence[str],
    outputs: Sequence[Mapping[str, Any]],
) -> tuple[BenchmarkOcrRecord, ...]:
    """Validate raw PP-OCRv5 region evidence without creating detector scores."""
    normalized_sample_ids = tuple(sample_ids)
    if not normalized_sample_ids:
        raise ValueError("OCR batch must contain at least one sample")
    if normalized_sample_ids != tuple(sorted(normalized_sample_ids)):
        raise ValueError("OCR batch sample_ids must use stable sorted order")
    if len(normalized_sample_ids) != len(set(normalized_sample_ids)):
        raise ValueError("OCR batch sample_ids must be unique")
    for sample_id in normalized_sample_ids:
        _validate_sample_id(sample_id, label="sample_id")
    if len(outputs) != len(normalized_sample_ids):
        raise ValueError("OCR output count does not match batch sample count")

    records: list[BenchmarkOcrRecord] = []
    expected_keys = {"regions", "text_area_ratio"}
    for sample_id, raw_output in zip(normalized_sample_ids, outputs, strict=True):
        if not isinstance(raw_output, Mapping):
            raise ValueError("OCR output must be an object")
        if set(raw_output) != expected_keys:
            raise ValueError("OCR output keys must be exactly regions and text_area_ratio")
        records.append(
            BenchmarkOcrRecord.model_validate(
                {
                    "schema_version": OCR_SCHEMA_VERSION,
                    "sample_id": sample_id,
                    "detector_model_id": OCR_DETECTOR_MODEL_ID,
                    "recognizer_model_id": OCR_RECOGNIZER_MODEL_ID,
                    **raw_output,
                }
            )
        )
    return tuple(records)


def validate_sidecar_outputs(
    *,
    run: BenchmarkSidecarRun,
    run_config: BenchmarkRunConfigV2,
    validated_inputs: Sequence[ValidatedBenchmarkInput],
    batches: Sequence[BenchmarkSidecarBatch | Mapping[str, Any]],
    scores: Sequence[DetectorScoreRecord | Mapping[str, Any]],
    ocr_records: Sequence[BenchmarkOcrRecord | Mapping[str, Any]] | None,
) -> None:
    """Validate record counts, schemas, order, and batch membership as one unit."""
    _validate_sidecar_run(run, run_config)
    expected_sample_ids = tuple(item.entry.sample_id for item in validated_inputs)
    if not expected_sample_ids:
        raise ValueError("validated inputs must not be empty")
    if expected_sample_ids != tuple(sorted(expected_sample_ids)):
        raise ValueError("validated inputs must use stable sample_id order")
    if len(expected_sample_ids) != len(set(expected_sample_ids)):
        raise ValueError("validated input sample_ids must be unique")

    expected_model_ids = tuple(model.model_id for model in run_config.models)
    parsed_batches = tuple(_parse_batch(batch) for batch in batches)
    _validate_batches(parsed_batches, run_config, expected_sample_ids)
    parsed_scores = tuple(_parse_score(score) for score in scores)
    _validate_scores(parsed_scores, parsed_batches, expected_model_ids, expected_sample_ids)
    if run.canonical_score_sha256 != canonical_score_sha256(parsed_scores):
        raise ValueError("run.json canonical_score_sha256 does not match raw detector scores")
    _validate_memory_measurements(parsed_batches, run)
    _validate_ocr_records(ocr_records, run_config, expected_sample_ids)


def _validate_supported_detector_models(run_config: BenchmarkRunConfigV2) -> None:
    unsupported = [
        model.model_id
        for model in run_config.models
        if model.model_id not in SUPPORTED_DETECTOR_MODEL_IDS
    ]
    if unsupported:
        raise ValueError(f"run config contains unsupported detector model_id values: {unsupported}")


def _configured_preflight_model_ids(run_config: BenchmarkRunConfigV2) -> tuple[str, ...]:
    model_ids = [model.model_id for model in run_config.models]
    if run_config.auxiliary_ocr is not None:
        model_ids.extend(
            (
                run_config.auxiliary_ocr.detector.model_id,
                run_config.auxiliary_ocr.recognizer.model_id,
            )
        )
    return tuple(model_ids)


def _validate_preflight_reports(
    run_config: BenchmarkRunConfigV2,
    reports: Sequence[DetectorPreflightReport],
) -> None:
    expected_model_ids = _configured_preflight_model_ids(run_config)
    report_ids = tuple(report.model_id for report in reports)
    if len(report_ids) != len(set(report_ids)):
        raise ValueError("preflight reports must not repeat model_id values")
    if set(report_ids) != set(expected_model_ids):
        raise ValueError("preflight reports must match configured model identities")
    by_model_id = {report.model_id: report for report in reports}
    for model_id in expected_model_ids:
        report = by_model_id[model_id]
        if report.status != "ready":
            raise ValueError(f"preflight report for {model_id} is not ready")
        if report.run_config_artifacts != "matched":
            raise ValueError(
                f"preflight report for {model_id} does not match the run-config artifact snapshot"
            )


def _safe_image_path(images_root: Path, entry: BenchmarkManifestEntry) -> Path:
    value = entry.image_path
    relative = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or relative.is_absolute()
        or "." in relative.parts
        or ".." in relative.parts
    ):
        raise ValueError(f"unsafe image_path for sample_id={entry.sample_id}")
    candidate = images_root.joinpath(*relative.parts)
    if candidate.is_symlink():
        raise ValueError(f"image for sample_id={entry.sample_id} must not be a symbolic link")
    if not candidate.exists():
        raise ValueError(f"image is missing for sample_id={entry.sample_id}")
    if not candidate.is_file():
        raise ValueError(f"image for sample_id={entry.sample_id} must be a regular file")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(images_root)
    except ValueError as error:
        raise ValueError(f"image for sample_id={entry.sample_id} escapes images_root") from error
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_image_decode(path: Path, sample_id: str) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as error:
        raise ValueError(f"image cannot be safely decoded for sample_id={sample_id}") from error


def _validate_sidecar_run(
    run: BenchmarkSidecarRun,
    run_config: BenchmarkRunConfigV2,
) -> None:
    _validate_supported_detector_models(run_config)
    expected_model_ids = tuple(model.model_id for model in run_config.models)
    if run.run_config_sha256 != run_config.canonical_sha256:
        raise ValueError("run.json run_config_sha256 does not match run config canonical hash")
    if run.manifest_sha256 != run_config.manifest_sha256:
        raise ValueError("run.json manifest_sha256 does not match run config")
    if run.detector_model_ids != expected_model_ids:
        raise ValueError("run.json detector_model_ids do not match run config order")
    if tuple(item.model_id for item in run.detector_provenance) != expected_model_ids:
        raise ValueError("run.json detector_provenance does not match run config order")
    for model, provenance in zip(
        run_config.models,
        run.detector_provenance,
        strict=True,
    ):
        observed_hashes = tuple(
            sorted(
                file.sha256
                for asset in provenance.assets
                if asset.role == "model"
                for file in asset.files
            )
        )
        if observed_hashes != tuple(sorted(model.artifact_sha256)):
            raise ValueError(
                "detector provenance model artifact SHA-256 values do not exactly "
                f"match run config for {model.model_id}"
            )
    if run.auxiliary_ocr_enabled != (run_config.auxiliary_ocr is not None):
        raise ValueError("run.json auxiliary_ocr_enabled does not match run config")
    _validate_auxiliary_ocr_provenance(run, run_config)
    if run.requested_device != run_config.device:
        raise ValueError("run.json requested_device does not match run config")
    if run.requested_precision != run_config.precision:
        raise ValueError("run.json requested_precision does not match run config")


def _validate_auxiliary_ocr_provenance(
    run: BenchmarkSidecarRun,
    run_config: BenchmarkRunConfigV2,
) -> None:
    if run_config.auxiliary_ocr is None:
        if run.auxiliary_ocr_provenance is not None:
            raise ValueError("run.json OCR provenance is only allowed with auxiliary_ocr")
        return
    provenance = run.auxiliary_ocr_provenance
    if provenance is None:
        raise ValueError("run.json OCR provenance is required with auxiliary_ocr")
    configured_assets = (
        run_config.auxiliary_ocr.detector,
        run_config.auxiliary_ocr.recognizer,
    )
    for asset, configured in zip(provenance.assets, configured_assets, strict=True):
        observed_hashes = tuple(sorted(file.sha256 for file in asset.files))
        if observed_hashes != tuple(sorted(configured.artifact_sha256)):
            raise ValueError(
                "OCR provenance model artifact SHA-256 values do not exactly match "
                f"run config for {configured.model_id}"
            )


def _parse_batch(batch: BenchmarkSidecarBatch | Mapping[str, Any]) -> BenchmarkSidecarBatch:
    if isinstance(batch, BenchmarkSidecarBatch):
        return BenchmarkSidecarBatch.model_validate(batch.model_dump(mode="python"))
    return BenchmarkSidecarBatch.model_validate(batch)


def _parse_score(score: DetectorScoreRecord | Mapping[str, Any]) -> DetectorScoreRecord:
    if isinstance(score, BaseModel):
        return _DETECTOR_SCORE_ADAPTER.validate_python(score.model_dump(mode="python"))
    return _DETECTOR_SCORE_ADAPTER.validate_python(score)


def _parse_ocr(record: BenchmarkOcrRecord | Mapping[str, Any]) -> BenchmarkOcrRecord:
    if isinstance(record, BenchmarkOcrRecord):
        return BenchmarkOcrRecord.model_validate(record.model_dump(mode="python"))
    return BenchmarkOcrRecord.model_validate(record)


def canonical_score_sha256(
    scores: Sequence[DetectorScoreRecord | Mapping[str, Any]],
) -> str:
    """Hash only stable model/sample identity and uncalibrated raw score payloads."""
    normalized: list[dict[str, Any]] = []
    for score in scores:
        parsed = _parse_score(score)
        payload = parsed.model_dump(mode="json")
        normalized.append(
            {
                "model_id": parsed.model_id,
                "sample_id": parsed.sample_id,
                "raw_scores": {
                    key: payload[key] for key in sorted(_RAW_OUTPUT_KEYS[parsed.model_id])
                },
            }
        )
    encoded = b"".join(
        json.dumps(
            item,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for item in sorted(normalized, key=lambda item: (item["model_id"], item["sample_id"]))
    )
    return hashlib.sha256(encoded).hexdigest()


def _validate_batches(
    batches: tuple[BenchmarkSidecarBatch, ...],
    run_config: BenchmarkRunConfigV2,
    expected_sample_ids: tuple[str, ...],
) -> None:
    batch_ids = [batch.batch_id for batch in batches]
    if len(batch_ids) != len(set(batch_ids)):
        raise ValueError("batches.jsonl batch_id values must be unique")
    expected_layout: list[tuple[str, int, tuple[str, ...]]] = []
    for model in run_config.models:
        for repeat in range(run_config.timing_repeats):
            for start in range(0, len(expected_sample_ids), model.batch_size):
                expected_layout.append(
                    (
                        model.model_id,
                        repeat,
                        expected_sample_ids[start : start + model.batch_size],
                    )
                )
    actual_layout = [(batch.model_id, batch.repeat, batch.sample_ids) for batch in batches]
    if actual_layout != expected_layout:
        raise ValueError(
            "batches.jsonl model order, repeat, batch_size, or sample_ids do not match run config"
        )


def _validate_scores(
    scores: tuple[DetectorScoreRecord, ...],
    batches: tuple[BenchmarkSidecarBatch, ...],
    expected_model_ids: tuple[str, ...],
    expected_sample_ids: tuple[str, ...],
) -> None:
    batches_by_id = {batch.batch_id: batch for batch in batches}
    expected_pairs = tuple(
        (model_id, sample_id)
        for model_id in expected_model_ids
        for sample_id in expected_sample_ids
    )
    actual_pairs = tuple((score.model_id, score.sample_id) for score in scores)
    if actual_pairs != expected_pairs:
        raise ValueError("scores.jsonl count, model order, or sample_ids do not match batches")
    for score in scores:
        batch = batches_by_id.get(score.batch_id)
        if batch is None:
            raise ValueError("scores.jsonl references an unknown batch_id")
        if batch.model_id != score.model_id:
            raise ValueError("scores.jsonl model_id does not match its batch")
        if batch.repeat != 0:
            raise ValueError("scores.jsonl may only reference the first timing repeat")
        if score.sample_id not in batch.sample_ids:
            raise ValueError("scores.jsonl sample_id is not a member of its batch")


def _validate_memory_measurements(
    batches: tuple[BenchmarkSidecarBatch, ...],
    run: BenchmarkSidecarRun,
) -> None:
    is_cuda = run.actual_device.casefold().startswith("cuda")
    if not is_cuda:
        if any(
            batch.memory_measurement_supported or batch.cuda_memory is not None
            for batch in batches
        ):
            raise ValueError("CPU sidecars must declare CUDA memory measurement as unsupported")
        return
    if any(
        not batch.memory_measurement_supported or batch.cuda_memory is None for batch in batches
    ):
        raise ValueError("CUDA sidecars require measured allocated and reserved memory")


def _validate_ocr_records(
    records: Sequence[BenchmarkOcrRecord | Mapping[str, Any]] | None,
    run_config: BenchmarkRunConfigV2,
    expected_sample_ids: tuple[str, ...],
) -> None:
    if run_config.auxiliary_ocr is None:
        if records is not None:
            raise ValueError("ocr.jsonl is only allowed when auxiliary_ocr is configured")
        return
    if records is None:
        raise ValueError("ocr.jsonl is required when auxiliary_ocr is configured")
    parsed = tuple(_parse_ocr(record) for record in records)
    if tuple(record.sample_id for record in parsed) != expected_sample_ids:
        raise ValueError("ocr.jsonl count or sample_ids do not match validated inputs")
    for record in parsed:
        if record.detector_model_id != run_config.auxiliary_ocr.detector.model_id:
            raise ValueError("ocr.jsonl detector_model_id does not match auxiliary_ocr")
        if record.recognizer_model_id != run_config.auxiliary_ocr.recognizer.model_id:
            raise ValueError("ocr.jsonl recognizer_model_id does not match auxiliary_ocr")
