"""Offline command-line orchestration for the detector benchmark harness."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import torch
from pydantic import ValidationError

from dataset_audit_studio.benchmarks.detector_adapters import (
    CommunityForensicsBenchmarkAdapter,
    PPOCRv5BenchmarkAdapter,
    UniversalFakeDetectBenchmarkAdapter,
    WatermarkSiglip2BenchmarkAdapter,
    WD14TaggerBenchmarkAdapter,
)
from dataset_audit_studio.benchmarks.detector_preflight import (
    DEFAULT_DETECTOR_ADAPTERS,
    DetectorPreflightReport,
    PreflightFileResult,
)
from dataset_audit_studio.benchmarks.harness import (
    AuxiliaryOcrBenchmarkAdapter,
    BenchmarkExecutionEnvironment,
    CudaMemorySnapshot,
    DetectorBenchmarkAdapter,
    run_detector_benchmark,
)
from dataset_audit_studio.benchmarks.manifest import load_manifest
from dataset_audit_studio.benchmarks.run_config import (
    OCR_DETECTOR_MODEL_ID,
    OCR_RECOGNIZER_MODEL_ID,
    RUN_CONFIG_SCHEMA_VERSION,
    RUN_CONFIG_V2_SCHEMA_VERSION,
    BenchmarkAuxiliaryModelReference,
    BenchmarkRunConfig,
    BenchmarkRunConfigV2,
)
from dataset_audit_studio.core.model_assets import AssetFile, ModelAsset, RuntimeAssets
from dataset_audit_studio.core.torch_runtime import resolve_torch_device
from dataset_audit_studio.model_adapters.registry import DEFAULT_REGISTRY
from dataset_audit_studio.model_adapters.validation import sha256_file, validate_file_container
from dataset_audit_studio.runtime import runtime_paths


@dataclass(frozen=True)
class BenchmarkCliDependencies:
    """Runtime composition injected into the thin CLI orchestration layer."""

    preflight_reports: Callable[
        [BenchmarkRunConfig | BenchmarkRunConfigV2], Sequence[DetectorPreflightReport]
    ]
    adapter_factories: Mapping[str, Callable[[], DetectorBenchmarkAdapter]]
    ocr_adapter_factory: Callable[[], AuxiliaryOcrBenchmarkAdapter] | None
    execution_environment: BenchmarkExecutionEnvironment
    clock_ns: Callable[[], int]


def main(
    argv: Sequence[str] | None = None,
    *,
    dependencies: BenchmarkCliDependencies | None = None,
) -> int:
    """Run the offline benchmark command and return its documented business status."""
    parser = _build_parser()
    namespace = parser.parse_args(argv)
    assert namespace.command == "run"
    assert namespace.offline is True

    try:
        manifest = load_manifest(namespace.manifest)
        run_config = _load_run_config(namespace.run_config)
        if isinstance(run_config, BenchmarkRunConfigV2):
            resolved_dependencies = dependencies or build_default_dependencies(run_config)
            preflight_reports = resolved_dependencies.preflight_reports(run_config)
        else:
            resolved_dependencies = dependencies or _v1_rejection_dependencies()
            preflight_reports = ()
        run_detector_benchmark(
            manifest=manifest,
            run_config=run_config,
            preflight_reports=preflight_reports,
            images_root=namespace.images_root,
            output_directory=namespace.output,
            adapter_factories=resolved_dependencies.adapter_factories,
            ocr_adapter_factory=resolved_dependencies.ocr_adapter_factory,
            execution_environment=resolved_dependencies.execution_environment,
            clock_ns=resolved_dependencies.clock_ns,
        )
    except Exception as error:
        print(f"detector benchmark failed: {error}", file=sys.stderr)
        return 1
    return 0


def build_default_dependencies(
    run_config: BenchmarkRunConfig | BenchmarkRunConfigV2,
) -> BenchmarkCliDependencies:
    """Compose only local, already-pinned assets; this function never downloads."""
    models_root = runtime_paths().models.resolve(strict=False)
    runtime_assets = _default_runtime_assets(models_root)
    execution_environment = _default_execution_environment(run_config)

    def preflight_reports(
        config: BenchmarkRunConfig | BenchmarkRunConfigV2,
    ) -> tuple[DetectorPreflightReport, ...]:
        adapters_by_model_id = {
            adapter.contract.model_id: adapter for adapter in DEFAULT_DETECTOR_ADAPTERS
        }
        reports = tuple(
            adapters_by_model_id[model.model_id].preflight(
                models_root=models_root,
                runtime_assets=runtime_assets,
                run_config=config,
            )
            for model in config.models
        )
        if not isinstance(config, BenchmarkRunConfigV2) or config.auxiliary_ocr is None:
            return reports
        return reports + (
            _preflight_runtime_asset(
                reference=config.auxiliary_ocr.detector,
                asset=runtime_assets.get(OCR_DETECTOR_MODEL_ID),
                models_root=models_root,
            ),
            _preflight_runtime_asset(
                reference=config.auxiliary_ocr.recognizer,
                asset=runtime_assets.get(OCR_RECOGNIZER_MODEL_ID),
                models_root=models_root,
            ),
        )

    return BenchmarkCliDependencies(
        preflight_reports=preflight_reports,
        adapter_factories={
            "universal_fake_detector_head": lambda: UniversalFakeDetectBenchmarkAdapter(
                runtime_assets=runtime_assets
            ),
            "commfor_model_384": CommunityForensicsBenchmarkAdapter,
            "watermark_siglip2": lambda: WatermarkSiglip2BenchmarkAdapter(
                runtime_assets=runtime_assets
            ),
            "wd14_eva02_large_v3": WD14TaggerBenchmarkAdapter,
        },
        ocr_adapter_factory=lambda: PPOCRv5BenchmarkAdapter(runtime_assets=runtime_assets),
        execution_environment=execution_environment,
        clock_ns=time.perf_counter_ns,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dataset-audit-detectors")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--run-config", type=Path, required=True)
    run.add_argument("--images-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--offline", action="store_true", required=True)
    return parser


def _v1_rejection_dependencies() -> BenchmarkCliDependencies:
    """Supply inert values only so the harness can publish its v1 rejection."""
    return BenchmarkCliDependencies(
        preflight_reports=lambda _config: (),
        adapter_factories={},
        ocr_adapter_factory=None,
        execution_environment=BenchmarkExecutionEnvironment(
            actual_device="cpu",
            actual_precision="float32",
            software={"runtime": "v1-rejection"},
            hardware={"host": "v1-rejection"},
            memory_measurement_supported=False,
        ),
        clock_ns=time.perf_counter_ns,
    )


def _load_run_config(path: Path) -> BenchmarkRunConfig | BenchmarkRunConfigV2:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"run config cannot be read: {path}") from error
    if not isinstance(raw, dict):
        raise ValueError("run config must contain an object")
    schema_version = raw.get("schema_version")
    try:
        if schema_version == RUN_CONFIG_SCHEMA_VERSION:
            return BenchmarkRunConfig.model_validate(raw)
        if schema_version == RUN_CONFIG_V2_SCHEMA_VERSION:
            return BenchmarkRunConfigV2.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"run config is invalid: {error}") from error
    raise ValueError("run config schema_version must be detector-benchmark-run/v1 or /v2")


def _default_runtime_assets(models_root: Path) -> RuntimeAssets:
    model_ids = (
        "universal_fake_detector_head",
        "openai_clip_vit_l14",
        "watermark_siglip2",
        OCR_DETECTOR_MODEL_ID,
        OCR_RECOGNIZER_MODEL_ID,
    )
    assets: list[ModelAsset] = []
    for model_id in model_ids:
        model = DEFAULT_REGISTRY.get(model_id)
        root = (
            models_root
            / "registry"
            / model.id
            / DEFAULT_REGISTRY.version_key(model)
        ).resolve(strict=False)
        files: list[AssetFile] = []
        for registered_file in model.files:
            path = root.joinpath(*PurePosixPath(registered_file.path).parts)
            try:
                stat = path.stat()
            except OSError:
                mtime_ns = 0
            else:
                mtime_ns = stat.st_mtime_ns if path.is_file() and not path.is_symlink() else 0
            files.append(
                AssetFile(
                    path=registered_file.path,
                    size=registered_file.size,
                    sha256=registered_file.sha256,
                    mtime_ns=mtime_ns,
                )
            )
        assets.append(
            ModelAsset(
                model_id=model.id,
                loader=model.loader,
                root=str(root),
                files=tuple(files),
                dependencies=model.dependencies,
                is_custom=False,
                base_model_id=None,
            )
        )
    return RuntimeAssets(models_root=str(models_root), models=tuple(assets))


def _preflight_runtime_asset(
    *,
    reference: BenchmarkAuxiliaryModelReference,
    asset: ModelAsset,
    models_root: Path,
) -> DetectorPreflightReport:
    root = Path(asset.root).resolve(strict=False)
    registered_model = DEFAULT_REGISTRY.get(reference.model_id)
    registered_files = {
        registered_file.path: registered_file
        for registered_file in registered_model.files
    }
    errors: list[str] = []
    files: list[PreflightFileResult] = []
    missing = False
    invalid = False
    try:
        root.relative_to(models_root.resolve(strict=False))
    except ValueError:
        errors.append("RuntimeAssets model root escapes models_root")
        invalid = True

    for expected in asset.files:
        path = root.joinpath(*PurePosixPath(expected.path).parts)
        if not path.exists():
            missing = True
            files.append(
                PreflightFileResult(
                    path=expected.path,
                    status="missing",
                    expected_size=expected.size,
                    actual_size=None,
                    expected_sha256=expected.sha256,
                    actual_sha256=None,
                )
            )
            continue
        if path.is_symlink() or not path.is_file():
            invalid = True
            files.append(
                PreflightFileResult(
                    path=expected.path,
                    status="unsafe",
                    expected_size=expected.size,
                    actual_size=None,
                    expected_sha256=expected.sha256,
                    actual_sha256=None,
                )
            )
            continue
        actual_size = path.stat().st_size
        if actual_size != expected.size:
            invalid = True
            files.append(
                PreflightFileResult(
                    path=expected.path,
                    status="size_mismatch",
                    expected_size=expected.size,
                    actual_size=actual_size,
                    expected_sha256=expected.sha256,
                    actual_sha256=None,
                )
            )
            continue
        actual_sha256 = sha256_file(path)
        if actual_sha256 != expected.sha256:
            invalid = True
            files.append(
                PreflightFileResult(
                    path=expected.path,
                    status="hash_mismatch",
                    expected_size=expected.size,
                    actual_size=actual_size,
                    expected_sha256=expected.sha256,
                    actual_sha256=actual_sha256,
                )
            )
            continue
        try:
            validate_file_container(path, registered_files[expected.path].format)
        except RuntimeError as error:
            invalid = True
            errors.append(f"invalid asset container: {expected.path}: {error}")
            files.append(
                PreflightFileResult(
                    path=expected.path,
                    status="unsafe",
                    expected_size=expected.size,
                    actual_size=actual_size,
                    expected_sha256=expected.sha256,
                    actual_sha256=actual_sha256,
                    detail=str(error),
                )
            )
            continue
        files.append(
            PreflightFileResult(
                path=expected.path,
                status="ready",
                expected_size=expected.size,
                actual_size=actual_size,
                expected_sha256=expected.sha256,
                actual_sha256=actual_sha256,
            )
        )

    ready_hashes = tuple(
        sorted(item.actual_sha256 for item in files if item.actual_sha256 is not None)
    )
    artifacts_matched = not missing and not invalid and ready_hashes == tuple(
        sorted(reference.artifact_sha256)
    )
    if not artifacts_matched and not missing and not invalid:
        errors.append("run-config artifact hash or model identity mismatch")
        invalid = True
    status = "invalid" if invalid or errors else "missing" if missing else "ready"
    return DetectorPreflightReport(
        model_id=reference.model_id,
        status=status,
        root=root if root.exists() else None,
        files=tuple(files),
        errors=tuple(errors),
        run_config_artifacts=(
            "matched" if status == "ready" and artifacts_matched else "not_evaluated"
        ),
    )


def _default_execution_environment(
    run_config: BenchmarkRunConfig | BenchmarkRunConfigV2,
) -> BenchmarkExecutionEnvironment:
    device = resolve_torch_device(run_config.device, run_config.precision)
    if device.type != "cuda":
        return BenchmarkExecutionEnvironment(
            actual_device=str(device),
            actual_precision=run_config.precision,
            software={"python": platform.python_version(), "torch": torch.__version__},
            hardware={"platform": platform.platform(), "device": str(device)},
            memory_measurement_supported=False,
        )

    def snapshot() -> CudaMemorySnapshot:
        return CudaMemorySnapshot(
            allocated_bytes=torch.cuda.memory_allocated(device),
            reserved_bytes=torch.cuda.memory_reserved(device),
        )

    def peak_snapshot() -> CudaMemorySnapshot:
        return CudaMemorySnapshot(
            allocated_bytes=torch.cuda.max_memory_allocated(device),
            reserved_bytes=torch.cuda.max_memory_reserved(device),
        )

    return BenchmarkExecutionEnvironment(
        actual_device=str(device),
        actual_precision=run_config.precision,
        software={"python": platform.python_version(), "torch": torch.__version__},
        hardware={"platform": platform.platform(), "device": str(device)},
        memory_measurement_supported=True,
        synchronize=lambda: torch.cuda.synchronize(device),
        current_memory=snapshot,
        reset_peak_memory=lambda: torch.cuda.reset_peak_memory_stats(device),
        peak_memory=peak_snapshot,
    )


if __name__ == "__main__":
    raise SystemExit(main())
