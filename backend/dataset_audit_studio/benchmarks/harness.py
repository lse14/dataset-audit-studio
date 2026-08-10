"""Injected, offline detector benchmark harness with atomic sidecar publication.

This module deliberately has no real detector adapters. B3.4 and B3.5 supply
those implementations; B3.3 only defines their lifecycle and measurement
boundary through a small injected protocol.
"""

from __future__ import annotations

import gc
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from dataset_audit_studio.benchmarks.detector_preflight import DetectorPreflightReport
from dataset_audit_studio.benchmarks.manifest import BenchmarkManifest
from dataset_audit_studio.benchmarks.run_config import (
    RUN_CONFIG_V2_SCHEMA_VERSION,
    BenchmarkRunConfig,
    BenchmarkRunConfigV2,
    validate_run_config_manifest,
)
from dataset_audit_studio.benchmarks.sidecar import (
    BATCH_SCHEMA_VERSION,
    FAILURE_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    BenchmarkCudaMemoryMeasurement,
    BenchmarkOcrRecord,
    BenchmarkSidecarAuxiliaryOcrProvenance,
    BenchmarkSidecarBatch,
    BenchmarkSidecarDetectorProvenance,
    BenchmarkSidecarFailure,
    BenchmarkSidecarRun,
    DetectorScoreRecord,
    ValidatedBenchmarkInput,
    canonical_score_sha256,
    validate_benchmark_inputs,
    validate_benchmark_preflight,
    validate_detector_batch_outputs,
    validate_ocr_batch_outputs,
    validate_sidecar_outputs,
)
from dataset_audit_studio.core.torch_runtime import Precision


@dataclass(frozen=True)
class CudaMemorySnapshot:
    """Allocated and reserved CUDA bytes captured at a single synchronization point."""

    allocated_bytes: int
    reserved_bytes: int


def _noop() -> None:
    return None


@dataclass(frozen=True)
class BenchmarkExecutionEnvironment:
    """Injected runtime facts and CUDA measurement hooks for one benchmark run."""

    actual_device: str
    actual_precision: Precision
    software: Mapping[str, str]
    hardware: Mapping[str, str]
    memory_measurement_supported: bool
    synchronize: Callable[[], None] = _noop
    current_memory: Callable[[], CudaMemorySnapshot] | None = None
    reset_peak_memory: Callable[[], None] | None = None
    peak_memory: Callable[[], CudaMemorySnapshot] | None = None


class DetectorBenchmarkAdapter(Protocol):
    """The deliberately small lifecycle required from a later real adapter."""

    model_id: str

    @property
    def provenance(self) -> BenchmarkSidecarDetectorProvenance | Mapping[str, Any]: ...

    def load(
        self,
        *,
        preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: Precision,
    ) -> None: ...

    def preprocess(self, images: Sequence[Image.Image]) -> Any: ...

    def transfer_to_device(
        self,
        prepared: Any,
        *,
        device: str,
        precision: Precision,
    ) -> Any: ...

    def forward(self, transferred: Any) -> Any: ...

    def to_cpu(self, outputs: Any) -> Sequence[Mapping[str, Any]]: ...

    def close(self) -> None:
        """Release partial or loaded resources; implementations must be idempotent."""


class AuxiliaryOcrBenchmarkAdapter(Protocol):
    """Independent OCR lifecycle that never contributes detector score rows."""

    model_id: str

    @property
    def provenance(self) -> BenchmarkSidecarAuxiliaryOcrProvenance | Mapping[str, Any]: ...

    def load(
        self,
        *,
        detector_preflight_report: DetectorPreflightReport,
        recognizer_preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: Precision,
        recognizer_batch_size: int,
    ) -> None: ...

    def preprocess(self, images: Sequence[Image.Image]) -> Any: ...

    def transfer_to_device(
        self,
        prepared: Any,
        *,
        device: str,
        precision: Precision,
    ) -> Any: ...

    def forward(self, transferred: Any) -> Any: ...

    def to_cpu(self, outputs: Any) -> Sequence[Mapping[str, Any]]: ...

    def close(self) -> None:
        """Release partial or loaded resources; implementations must be idempotent."""


@dataclass(frozen=True)
class DetectorBenchmarkHarnessResult:
    output_directory: Path
    run: BenchmarkSidecarRun


def run_detector_benchmark(
    *,
    manifest: BenchmarkManifest,
    run_config: BenchmarkRunConfig | BenchmarkRunConfigV2,
    preflight_reports: Sequence[DetectorPreflightReport],
    images_root: Path,
    output_directory: Path,
    adapter_factories: Mapping[str, Callable[[], DetectorBenchmarkAdapter]],
    ocr_adapter_factory: Callable[[], AuxiliaryOcrBenchmarkAdapter] | None = None,
    execution_environment: BenchmarkExecutionEnvironment,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> DetectorBenchmarkHarnessResult:
    """Run injected adapters only after complete offline preflight/input validation."""
    output_path = Path(output_directory)
    stage = "publication"
    current_model_id: str | None = None
    current_batch_id: str | None = None
    completed_scores: list[DetectorScoreRecord] = []
    completed_batches: list[BenchmarkSidecarBatch] = []
    completed_ocr_records: list[BenchmarkOcrRecord] | None = None
    temporary_directory: Path | None = None
    try:
        _require_output_parent(output_path.parent)
        _require_empty_output_target(output_path)

        stage = "preflight"
        _require_v2_run_config(run_config)
        validate_run_config_manifest(run_config, manifest)
        validate_benchmark_preflight(
            run_config=run_config,
            preflight_reports=preflight_reports,
        )

        stage = "input_validation"
        validated_inputs = validate_benchmark_inputs(
            manifest=manifest,
            run_config=run_config,
            preflight_reports=preflight_reports,
            images_root=images_root,
        )
        _validate_execution_environment(execution_environment)
        _require_ocr_adapter_factory(run_config, ocr_adapter_factory)

        reports_by_model_id = {report.model_id: report for report in preflight_reports}
        provenance: list[BenchmarkSidecarDetectorProvenance] = []
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=f".{output_path.name}-", dir=output_path.parent)
        )
        for model in run_config.models:
            current_model_id = model.model_id
            current_batch_id = None
            stage = "adapter_load"
            adapter = _construct_adapter(adapter_factories, model.model_id)
            try:
                if adapter.model_id != model.model_id:
                    raise ValueError("adapter model_id does not match the run config")
                adapter.load(
                    preflight_report=reports_by_model_id[model.model_id],
                    requested_device=run_config.device,
                    requested_precision=run_config.precision,
                )
                adapter_provenance = BenchmarkSidecarDetectorProvenance.model_validate(
                    adapter.provenance
                )
                if adapter_provenance.model_id != model.model_id:
                    raise ValueError("adapter provenance model_id does not match the run config")
                provenance.append(adapter_provenance)

                batches = _partition_inputs(validated_inputs, model.batch_size)
                first_batch = batches[0]
                for _ in range(run_config.warmup_batches):
                    stage = "execution"
                    current_batch_id = None
                    decoded = _decode_batch_images([item.path for item in first_batch])
                    _execute_adapter_batch(
                        adapter=adapter,
                        images=decoded,
                        environment=execution_environment,
                        clock_ns=clock_ns,
                        measure=False,
                    )

                for repeat in range(run_config.timing_repeats):
                    for batch_index, batch_inputs in enumerate(batches):
                        current_batch_id = _batch_id(model.model_id, repeat, batch_index)
                        stage = "execution"
                        decoded = _decode_batch_images([item.path for item in batch_inputs])
                        raw_outputs, elapsed_ms, cuda_memory = _execute_adapter_batch(
                            adapter=adapter,
                            images=decoded,
                            environment=execution_environment,
                            clock_ns=clock_ns,
                            measure=True,
                        )
                        stage = "output_validation"
                        sample_ids = tuple(item.entry.sample_id for item in batch_inputs)
                        records = validate_detector_batch_outputs(
                            model_id=model.model_id,
                            batch_id=current_batch_id,
                            sample_ids=sample_ids,
                            outputs=raw_outputs,
                        )
                        batch_record = BenchmarkSidecarBatch(
                            schema_version=BATCH_SCHEMA_VERSION,
                            batch_id=current_batch_id,
                            model_id=model.model_id,
                            sample_ids=sample_ids,
                            repeat=repeat,
                            batch_size=len(sample_ids),
                            end_to_end_duration_ms=elapsed_ms,
                            memory_measurement_supported=(cuda_memory is not None),
                            cuda_memory=cuda_memory,
                        )
                        completed_batches.append(batch_record)
                        if repeat == 0:
                            completed_scores.extend(records)
            finally:
                if sys.exc_info()[1] is None:
                    stage = "execution"
                _close_adapter_preserving_original_error(adapter)
                del adapter
                gc.collect()

        ocr_provenance: BenchmarkSidecarAuxiliaryOcrProvenance | None = None
        if run_config.auxiliary_ocr is not None:
            current_model_id = run_config.auxiliary_ocr.detector.model_id
            current_batch_id = None
            stage = "adapter_load"
            ocr_adapter = _construct_ocr_adapter(ocr_adapter_factory)
            try:
                ocr_adapter.load(
                    detector_preflight_report=reports_by_model_id[
                        run_config.auxiliary_ocr.detector.model_id
                    ],
                    recognizer_preflight_report=reports_by_model_id[
                        run_config.auxiliary_ocr.recognizer.model_id
                    ],
                    requested_device=run_config.device,
                    requested_precision=run_config.precision,
                    recognizer_batch_size=run_config.auxiliary_ocr.recognizer.batch_size,
                )
                ocr_provenance = BenchmarkSidecarAuxiliaryOcrProvenance.model_validate(
                    ocr_adapter.provenance
                )
                batches = _partition_inputs(
                    validated_inputs,
                    run_config.auxiliary_ocr.detector.batch_size,
                )
                first_batch = batches[0]
                for _ in range(run_config.warmup_batches):
                    stage = "execution"
                    current_batch_id = None
                    decoded = _decode_batch_images([item.path for item in first_batch])
                    _execute_adapter_batch(
                        adapter=ocr_adapter,
                        images=decoded,
                        environment=execution_environment,
                        clock_ns=clock_ns,
                        measure=False,
                    )

                completed_ocr_records = []
                for repeat in range(run_config.timing_repeats):
                    for batch_index, batch_inputs in enumerate(batches):
                        current_batch_id = _batch_id(
                            run_config.auxiliary_ocr.detector.model_id,
                            repeat,
                            batch_index,
                        )
                        stage = "execution"
                        decoded = _decode_batch_images([item.path for item in batch_inputs])
                        raw_outputs, _elapsed_ms, _cuda_memory = _execute_adapter_batch(
                            adapter=ocr_adapter,
                            images=decoded,
                            environment=execution_environment,
                            clock_ns=clock_ns,
                            measure=True,
                        )
                        stage = "output_validation"
                        sample_ids = tuple(item.entry.sample_id for item in batch_inputs)
                        records = validate_ocr_batch_outputs(
                            sample_ids=sample_ids,
                            outputs=raw_outputs,
                        )
                        if repeat == 0:
                            completed_ocr_records.extend(records)
            finally:
                if sys.exc_info()[1] is None:
                    stage = "execution"
                _close_adapter_preserving_original_error(ocr_adapter)
                del ocr_adapter
                gc.collect()

        current_model_id = None
        current_batch_id = None
        stage = "output_validation"
        run = BenchmarkSidecarRun(
            schema_version=RUN_SCHEMA_VERSION,
            run_config_schema_version=RUN_CONFIG_V2_SCHEMA_VERSION,
            run_config_sha256=run_config.canonical_sha256,
            manifest_sha256=run_config.manifest_sha256,
            canonical_score_sha256=canonical_score_sha256(completed_scores),
            detector_model_ids=tuple(model.model_id for model in run_config.models),
            detector_provenance=tuple(provenance),
            auxiliary_ocr_enabled=(run_config.auxiliary_ocr is not None),
            auxiliary_ocr_provenance=ocr_provenance,
            report_only=True,
            requested_device=run_config.device,
            actual_device=execution_environment.actual_device,
            requested_precision=run_config.precision,
            actual_precision=execution_environment.actual_precision,
            software=dict(execution_environment.software),
            hardware=dict(execution_environment.hardware),
        )
        validate_sidecar_outputs(
            run=run,
            run_config=run_config,
            validated_inputs=validated_inputs,
            batches=completed_batches,
            scores=completed_scores,
            ocr_records=completed_ocr_records,
        )

        current_model_id = None
        current_batch_id = None
        stage = "publication"
        _write_success_sidecars(
            temporary_directory=temporary_directory,
            run=run,
            batches=completed_batches,
            scores=completed_scores,
            ocr_records=completed_ocr_records,
        )
        _require_empty_output_target(output_path)
        os.replace(temporary_directory, output_path)
        temporary_directory = None
        return DetectorBenchmarkHarnessResult(output_directory=output_path, run=run)
    except Exception as error:
        if temporary_directory is not None:
            shutil.rmtree(temporary_directory, ignore_errors=True)
        failure_path = _failure_sidecar_path(output_path)
        if (
            not output_path.exists()
            and output_path.parent.exists()
            and output_path.parent.is_dir()
            and not failure_path.exists()
        ):
            with suppress(FileExistsError):
                _publish_failure(
                    output_path=output_path,
                    stage=stage,
                    message=str(error),
                    model_id=current_model_id,
                    batch_id=current_batch_id,
                    exception_type=type(error).__name__,
                    completed_score_count=len(completed_scores),
                    completed_batch_count=len(completed_batches),
                )
        raise


def _require_empty_output_target(output_path: Path) -> None:
    failure_path = _failure_sidecar_path(output_path)
    if failure_path.exists():
        raise FileExistsError(f"benchmark failure sidecar already exists: {failure_path}")
    if output_path.exists():
        raise FileExistsError(f"benchmark output target already exists: {output_path}")


def _require_output_parent(parent: Path) -> None:
    if not parent.exists() or not parent.is_dir():
        raise ValueError("benchmark output parent must be an existing directory")


def _require_v2_run_config(
    run_config: BenchmarkRunConfig | BenchmarkRunConfigV2,
) -> BenchmarkRunConfigV2:
    if not isinstance(run_config, BenchmarkRunConfigV2):
        raise ValueError("detector benchmark harness requires a detector-benchmark-run/v2 config")
    if run_config.schema_version != RUN_CONFIG_V2_SCHEMA_VERSION:
        raise ValueError("detector benchmark harness requires a detector-benchmark-run/v2 config")
    return run_config


def _require_ocr_adapter_factory(
    run_config: BenchmarkRunConfigV2,
    factory: Callable[[], AuxiliaryOcrBenchmarkAdapter] | None,
) -> None:
    if run_config.auxiliary_ocr is not None and factory is None:
        raise ValueError("auxiliary_ocr requires an injected OCR benchmark adapter factory")


def _validate_execution_environment(environment: BenchmarkExecutionEnvironment) -> None:
    actual_device = environment.actual_device.casefold()
    if actual_device == "cpu":
        if environment.memory_measurement_supported:
            raise ValueError("CPU execution must not claim CUDA memory measurement support")
        return
    if not actual_device.startswith("cuda"):
        raise ValueError("benchmark execution device must be CPU or CUDA")
    if not environment.memory_measurement_supported:
        raise ValueError("CUDA execution requires memory measurement support")
    if (
        environment.current_memory is None
        or environment.reset_peak_memory is None
        or environment.peak_memory is None
    ):
        raise ValueError("CUDA execution requires allocated and reserved memory hooks")


def _construct_adapter(
    factories: Mapping[str, Callable[[], DetectorBenchmarkAdapter]],
    model_id: str,
) -> DetectorBenchmarkAdapter:
    try:
        factory = factories[model_id]
    except KeyError as error:
        raise ValueError(f"benchmark adapter factory is missing for {model_id}") from error
    return factory()


def _construct_ocr_adapter(
    factory: Callable[[], AuxiliaryOcrBenchmarkAdapter] | None,
) -> AuxiliaryOcrBenchmarkAdapter:
    if factory is None:
        raise ValueError("auxiliary_ocr requires an injected OCR benchmark adapter factory")
    return factory()


def _close_adapter_preserving_original_error(adapter: DetectorBenchmarkAdapter) -> None:
    """Close a constructed adapter without replacing an already-active error."""
    original_error = sys.exc_info()[1]
    try:
        adapter.close()
    except BaseException:
        if original_error is None:
            raise


def _partition_inputs(
    validated_inputs: Sequence[ValidatedBenchmarkInput],
    batch_size: int,
) -> tuple[tuple[ValidatedBenchmarkInput, ...], ...]:
    batches = tuple(
        tuple(validated_inputs[index : index + batch_size])
        for index in range(0, len(validated_inputs), batch_size)
    )
    if not batches:
        raise ValueError("validated inputs must not be empty")
    return batches


def _decode_batch_images(paths: Sequence[Path]) -> list[Image.Image]:
    """Read and decode batch images before the timed detector boundary begins."""
    images: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as image:
            images.append(image.convert("RGB").copy())
    return images


def _execute_adapter_batch(
    *,
    adapter: DetectorBenchmarkAdapter | AuxiliaryOcrBenchmarkAdapter,
    images: Sequence[Image.Image],
    environment: BenchmarkExecutionEnvironment,
    clock_ns: Callable[[], int],
    measure: bool,
) -> tuple[Sequence[Mapping[str, Any]], float | None, BenchmarkCudaMemoryMeasurement | None]:
    is_cuda = environment.actual_device.casefold().startswith("cuda")
    cuda_memory: BenchmarkCudaMemoryMeasurement | None = None
    if not measure:
        if is_cuda:
            environment.synchronize()
        _run_adapter_pipeline(adapter, images, environment)
        if is_cuda:
            environment.synchronize()
        return (), None, None

    if is_cuda:
        assert environment.current_memory is not None
        assert environment.reset_peak_memory is not None
        assert environment.peak_memory is not None
        environment.synchronize()
        baseline = environment.current_memory()
        environment.reset_peak_memory()
    start_ns = clock_ns()
    raw_outputs = _run_adapter_pipeline(adapter, images, environment)
    if is_cuda:
        environment.synchronize()
    end_ns = clock_ns()
    if end_ns < start_ns:
        raise ValueError("benchmark clock must not move backwards")
    if is_cuda:
        peak = environment.peak_memory()
        cuda_memory = BenchmarkCudaMemoryMeasurement(
            baseline_allocated_bytes=baseline.allocated_bytes,
            baseline_reserved_bytes=baseline.reserved_bytes,
            peak_allocated_bytes=peak.allocated_bytes,
            peak_reserved_bytes=peak.reserved_bytes,
        )
    return raw_outputs, (end_ns - start_ns) / 1_000_000, cuda_memory


def _run_adapter_pipeline(
    adapter: DetectorBenchmarkAdapter | AuxiliaryOcrBenchmarkAdapter,
    images: Sequence[Image.Image],
    environment: BenchmarkExecutionEnvironment,
) -> Sequence[Mapping[str, Any]]:
    prepared = adapter.preprocess(images)
    transferred = adapter.transfer_to_device(
        prepared,
        device=environment.actual_device,
        precision=environment.actual_precision,
    )
    return adapter.to_cpu(adapter.forward(transferred))


def _batch_id(model_id: str, repeat: int, batch_index: int) -> str:
    return f"{model_id}-repeat-{repeat:03d}-batch-{batch_index:03d}"


def _write_success_sidecars(
    *,
    temporary_directory: Path,
    run: BenchmarkSidecarRun,
    batches: Sequence[BenchmarkSidecarBatch],
    scores: Sequence[DetectorScoreRecord],
    ocr_records: Sequence[BenchmarkOcrRecord] | None,
) -> None:
    _write_json(temporary_directory / "run.json", run.model_dump(mode="json"))
    _write_jsonl(
        temporary_directory / "batches.jsonl",
        [batch.model_dump(mode="json") for batch in batches],
    )
    _write_jsonl(
        temporary_directory / "scores.jsonl",
        [score.model_dump(mode="json") for score in scores],
    )
    if ocr_records is not None:
        _write_jsonl(
            temporary_directory / "ocr.jsonl",
            [record.model_dump(mode="json") for record in ocr_records],
        )


def _publish_failure(
    *,
    output_path: Path,
    stage: str,
    message: str,
    model_id: str | None,
    batch_id: str | None,
    exception_type: str,
    completed_score_count: int,
    completed_batch_count: int,
) -> None:
    failure = BenchmarkSidecarFailure(
        schema_version=FAILURE_SCHEMA_VERSION,
        stage=stage,
        message=message or exception_type,
        model_id=model_id,
        batch_id=batch_id,
        exception_type=exception_type,
        completed_score_count=completed_score_count,
        completed_batch_count=completed_batch_count,
    )
    failure_path = _failure_sidecar_path(output_path)
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.name}-failure-",
            suffix=".json",
            dir=output_path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(_encode_json(failure.model_dump(mode="json")))
        os.link(temporary_path, failure_path)
        temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _failure_sidecar_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.failure.json")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(_encode_json(value), encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(_encode_json(value) for value in values),
        encoding="utf-8",
        newline="\n",
    )


def _encode_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
