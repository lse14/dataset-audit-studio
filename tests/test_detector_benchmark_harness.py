from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from dataset_audit_studio.benchmarks.detector_preflight import DetectorPreflightReport
from dataset_audit_studio.benchmarks.manifest import BenchmarkManifest, BenchmarkManifestEntry
from dataset_audit_studio.benchmarks.run_config import BenchmarkRunConfig, BenchmarkRunConfigV2
from dataset_audit_studio.benchmarks.sidecar import WD14_TAG_SCORE_LABELS
from PIL import Image


def _api() -> dict[str, Any]:
    try:
        import dataset_audit_studio.benchmarks.harness as harness
        from dataset_audit_studio.benchmarks.harness import (
            BenchmarkExecutionEnvironment,
            CudaMemorySnapshot,
            run_detector_benchmark,
        )
    except ImportError as error:
        pytest.fail(f"Detector benchmark harness API is not implemented: {error}")
    return {
        "BenchmarkExecutionEnvironment": BenchmarkExecutionEnvironment,
        "CudaMemorySnapshot": CudaMemorySnapshot,
        "module": harness,
        "run_detector_benchmark": run_detector_benchmark,
    }


def _annotation(value: str) -> dict[str, str]:
    return {
        "value": value,
        "label_source": "manual:benchmark-review-v1",
        "label_trust": "trusted",
    }


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (5, 3), color) as image:
        image.save(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(path: str, image_sha256: str, sample_id: str) -> BenchmarkManifestEntry:
    return BenchmarkManifestEntry.model_validate(
        {
            "schema_version": "detector-benchmark-manifest/v1",
            "sample_id": sample_id,
            "image_path": path,
            "image_sha256": image_sha256,
            "source_corpus": "danbooru",
            "strata": ["human_anime", "ordinary_text"],
            "ai_origin": _annotation("human"),
            "watermark_labels": {
                "watermark": _annotation("absent"),
                "signature": _annotation("absent"),
                "logo": _annotation("absent"),
                "artist_logo": _annotation("absent"),
                "sample_watermark": _annotation("absent"),
                "text": _annotation("present"),
            },
        }
    )


def _manifest_with_images(tmp_path: Path) -> tuple[Path, BenchmarkManifest]:
    images_root = tmp_path / "images"
    first = images_root / "nested" / "sample-001.png"
    second = images_root / "nested" / "sample-002.png"
    _write_image(first, (20, 30, 40))
    _write_image(second, (40, 30, 20))
    return images_root, BenchmarkManifest(
        entries=(
            _entry("nested/sample-002.png", _sha256(second), "sample-002"),
            _entry("nested/sample-001.png", _sha256(first), "sample-001"),
        )
    )


def _model_reference(
    model_id: str,
    *,
    role: str,
    artifact_character: str,
    batch_size: int,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "role": role,
        "source_kind": "huggingface",
        "source_repository": "Example/Offline-Detector",
        "revision": "a" * 40,
        "artifact_sha256": [artifact_character * 64],
        "declared_license": "MIT",
        "remote_code_allowed": False,
        "batch_size": batch_size,
    }


def _config(
    manifest: BenchmarkManifest,
    *,
    model_ids: tuple[str, ...] = (
        "universal_fake_detector_head",
        "commfor_model_384",
        "watermark_siglip2",
        "wd14_eva02_large_v3",
    ),
    batch_size: int = 1,
    warmup_batches: int = 1,
    timing_repeats: int = 2,
    device: str = "cpu",
) -> BenchmarkRunConfigV2:
    return BenchmarkRunConfigV2.model_validate(
        {
            "schema_version": "detector-benchmark-run/v2",
            "manifest_sha256": manifest.canonical_sha256,
            "seed": 7,
            "review_top_k": 5,
            "device": device,
            "precision": "float32",
            "offline": True,
            "report_only": True,
            "warmup_batches": warmup_batches,
            "timing_repeats": timing_repeats,
            "models": [
                _model_reference(
                    model_id,
                    role="baseline" if index == 0 else "candidate",
                    artifact_character=chr(ord("b") + index),
                    batch_size=batch_size,
                )
                for index, model_id in enumerate(model_ids)
            ],
        }
    )


def _ready_reports(config: BenchmarkRunConfigV2) -> tuple[DetectorPreflightReport, ...]:
    return tuple(
        DetectorPreflightReport(
            model_id=model.model_id,
            status="ready",
            root=Path("."),
            files=(),
            errors=(),
            run_config_artifacts="matched",
        )
        for model in config.models
    )


def _raw_output(model_id: str) -> dict[str, Any]:
    if model_id in {"universal_fake_detector_head", "commfor_model_384"}:
        return {"raw_sigmoid_score": 0.25}
    if model_id == "watermark_siglip2":
        return {
            "raw_softmax_label_score": 0.75,
            "raw_softmax_label_scores": {"Watermark": 0.75, "Clean": 0.25},
        }
    if model_id == "wd14_eva02_large_v3":
        return {"raw_sigmoid_tag_scores": {tag: 0.5 for tag in WD14_TAG_SCORE_LABELS}}
    raise AssertionError(f"Unexpected test model id: {model_id}")


@dataclass
class _Trace:
    events: list[str] = field(default_factory=list)
    active_model_id: str | None = None
    max_active_models: int = 0
    factory_calls: list[str] = field(default_factory=list)


class _FakeAdapter:
    def __init__(
        self,
        *,
        model_id: str,
        trace: _Trace,
        provenance_sha256: str,
        fail_forward_at: int | None = None,
        fail_load_after_allocation: bool = False,
        fail_close: bool = False,
        invalid_outputs: bool = False,
    ) -> None:
        self.model_id = model_id
        self._trace = trace
        self._provenance_sha256 = provenance_sha256
        self._fail_forward_at = fail_forward_at
        self._fail_load_after_allocation = fail_load_after_allocation
        self._fail_close = fail_close
        self._invalid_outputs = invalid_outputs
        self._forward_calls = 0
        self._closed = False

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "assets": [
                {
                    "asset_id": self.model_id,
                    "role": "model",
                    "files": [
                        {
                            "path": "model.safetensors",
                            "sha256": self._provenance_sha256,
                            "size_bytes": 123,
                        }
                    ],
                },
                {
                    "asset_id": "shared_dependency",
                    "role": "dependency",
                    "files": [
                        {
                            "path": "dependency.bin",
                            "sha256": "c" * 64,
                            "size_bytes": 17,
                        }
                    ],
                },
            ],
            "preprocessing": {
                "source": "tests/fake-preprocessor",
                "revision": "test-v1",
            },
        }

    def load(
        self,
        *,
        preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: str,
    ) -> None:
        assert preflight_report.model_id == self.model_id
        assert self._trace.active_model_id is None
        self._trace.active_model_id = self.model_id
        self._trace.max_active_models = max(self._trace.max_active_models, 1)
        self._trace.events.append(f"load:{self.model_id}:{requested_device}:{requested_precision}")
        if self._fail_load_after_allocation:
            self._trace.events.append(f"partial-allocation:{self.model_id}")
            raise RuntimeError("synthetic partial load failure")

    def preprocess(self, images: Sequence[Image.Image]) -> list[tuple[int, int]]:
        assert self._trace.active_model_id == self.model_id
        self._trace.events.append(f"preprocess:{self.model_id}")
        return [image.size for image in images]

    def transfer_to_device(
        self,
        prepared: list[tuple[int, int]],
        *,
        device: str,
        precision: str,
    ) -> list[tuple[int, int]]:
        assert self._trace.active_model_id == self.model_id
        self._trace.events.append(f"transfer:{self.model_id}:{device}:{precision}")
        return prepared

    def forward(self, transferred: list[tuple[int, int]]) -> list[dict[str, Any]]:
        assert self._trace.active_model_id == self.model_id
        self._forward_calls += 1
        self._trace.events.append(f"forward:{self.model_id}")
        if self._fail_forward_at == self._forward_calls:
            raise RuntimeError("synthetic forward failure")
        if self._invalid_outputs:
            return [{"invalid_raw_output": 1.0} for _ in transferred]
        return [_raw_output(self.model_id) for _ in transferred]

    def to_cpu(self, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assert self._trace.active_model_id == self.model_id
        self._trace.events.append(f"to_cpu:{self.model_id}")
        return outputs

    def close(self) -> None:
        if self._closed:
            return
        assert self._trace.active_model_id == self.model_id
        self._trace.events.append(f"close:{self.model_id}")
        self._trace.active_model_id = None
        self._closed = True
        if self._fail_close:
            raise RuntimeError("synthetic close failure")


class _FakeClock:
    def __init__(self, trace: _Trace, *, step_ns: int = 1_000_000) -> None:
        self._trace = trace
        self._next_ns = 0
        self._step_ns = step_ns

    def __call__(self) -> int:
        self._trace.events.append("clock")
        value = self._next_ns
        self._next_ns += self._step_ns
        return value


def _factories(
    config: BenchmarkRunConfigV2,
    trace: _Trace,
    *,
    failing_model_id: str | None = None,
    fail_forward_at: int | None = None,
    fail_load_after_allocation_model_id: str | None = None,
    fail_close_model_id: str | None = None,
    invalid_outputs_model_id: str | None = None,
    provenance_sha256_by_model_id: Mapping[str, str] | None = None,
) -> dict[str, Callable[[], _FakeAdapter]]:
    factories: dict[str, Callable[[], _FakeAdapter]] = {}
    for model in config.models:
        model_id = model.model_id

        def factory(
            model_id: str = model_id,
            artifact_sha256: str = model.artifact_sha256[0],
        ) -> _FakeAdapter:
            trace.factory_calls.append(model_id)
            return _FakeAdapter(
                model_id=model_id,
                trace=trace,
                provenance_sha256=(
                    provenance_sha256_by_model_id[model_id]
                    if provenance_sha256_by_model_id is not None
                    and model_id in provenance_sha256_by_model_id
                    else artifact_sha256
                ),
                fail_forward_at=fail_forward_at if model_id == failing_model_id else None,
                fail_load_after_allocation=(
                    model_id == fail_load_after_allocation_model_id
                ),
                fail_close=model_id == fail_close_model_id,
                invalid_outputs=model_id == invalid_outputs_model_id,
            )

        factories[model_id] = factory
    return factories


def _cpu_environment(api: dict[str, Any], trace: _Trace, *, label: str = "cpu-a") -> Any:
    return api["BenchmarkExecutionEnvironment"](
        actual_device="cpu",
        actual_precision="float32",
        software={"runtime": "pytest"},
        hardware={"processor": label},
        memory_measurement_supported=False,
        synchronize=lambda: trace.events.append("sync"),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_harness_processes_one_model_at_a_time_and_publishes_repeated_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(manifest)
    trace = _Trace()
    output_directory = tmp_path / "detector-results"
    original_decode = api["module"]._decode_batch_images

    def record_decode(paths: Sequence[Path]) -> list[Image.Image]:
        trace.events.append("decode")
        return original_decode(paths)

    monkeypatch.setattr(api["module"], "_decode_batch_images", record_decode)
    api["run_detector_benchmark"](
        manifest=manifest,
        run_config=config,
        preflight_reports=_ready_reports(config),
        images_root=images_root,
        output_directory=output_directory,
        adapter_factories=_factories(config, trace),
        execution_environment=_cpu_environment(api, trace),
        clock_ns=_FakeClock(trace),
    )

    model_ids = [model.model_id for model in config.models]
    assert trace.factory_calls == model_ids
    loaded_ids = [event.split(":")[1] for event in trace.events if event.startswith("load:")]
    closed_ids = [event.split(":")[1] for event in trace.events if event.startswith("close:")]
    assert loaded_ids == model_ids
    assert closed_ids == model_ids
    assert trace.max_active_models == 1
    for index, previous in enumerate(model_ids[:-1]):
        following = model_ids[index + 1]
        assert trace.events.index(f"close:{previous}") < next(
            index
            for index, event in enumerate(trace.events)
            if event.startswith(f"load:{following}:")
        )
    first_clock = trace.events.index("clock")
    assert trace.events.index("decode") < first_clock
    assert first_clock < next(
        index
        for index, event in enumerate(trace.events)
        if index > first_clock and event == f"preprocess:{model_ids[0]}"
    )

    run = json.loads((output_directory / "run.json").read_text(encoding="utf-8"))
    batches = _read_jsonl(output_directory / "batches.jsonl")
    scores = _read_jsonl(output_directory / "scores.jsonl")
    assert run["requested_device"] == "cpu"
    assert run["actual_device"] == "cpu"
    assert run["requested_precision"] == "float32"
    assert run["actual_precision"] == "float32"
    assert run["software"] == {"runtime": "pytest"}
    assert run["hardware"] == {"processor": "cpu-a"}
    assert [item["model_id"] for item in run["detector_provenance"]] == model_ids
    assert run["detector_provenance"][0]["assets"][0]["files"][0]["size_bytes"] == 123
    assert run["detector_provenance"][0]["assets"][1]["role"] == "dependency"
    assert run["detector_provenance"][0]["preprocessing"] == {
        "source": "tests/fake-preprocessor",
        "revision": "test-v1",
    }
    assert len(batches) == len(model_ids) * config.timing_repeats * 2
    assert [(batch["model_id"], batch["repeat"]) for batch in batches] == [
        (model_id, repeat)
        for model_id in model_ids
        for repeat in range(config.timing_repeats)
        for _ in range(2)
    ]
    assert all(batch["batch_size"] == 1 for batch in batches)
    assert all(batch["end_to_end_duration_ms"] > 0 for batch in batches)
    assert all(batch["memory_measurement_supported"] is False for batch in batches)
    assert all(batch["cuda_memory"] is None for batch in batches)
    assert len(scores) == len(model_ids) * 2
    assert [(score["model_id"], score["sample_id"]) for score in scores] == [
        (model_id, sample_id)
        for model_id in model_ids
        for sample_id in ("sample-001", "sample-002")
    ]
    assert all("repeat-000" in score["batch_id"] for score in scores)
    assert not list(tmp_path.glob(".detector-results-*"))


def test_harness_validates_preflight_and_images_before_constructing_adapters(
    tmp_path: Path,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        warmup_batches=0,
        timing_repeats=1,
    )
    trace = _Trace()
    reports = list(_ready_reports(config))
    reports[0] = replace(reports[0], status="missing")
    preflight_output = tmp_path / "preflight-output"

    with pytest.raises(ValueError, match="preflight report"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=tuple(reports),
            images_root=images_root,
            output_directory=preflight_output,
            adapter_factories=_factories(config, trace),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    assert trace.factory_calls == []
    assert not preflight_output.exists()
    preflight_failure = json.loads(
        (tmp_path / "preflight-output.failure.json").read_text(encoding="utf-8")
    )
    assert preflight_failure["stage"] == "preflight"
    assert preflight_failure["exception_type"] == "ValueError"
    assert preflight_failure["completed_score_count"] == 0

    corrupt_path = images_root / "nested" / "sample-001.png"
    corrupt_path.write_bytes(b"not a valid image")
    input_output = tmp_path / "input-output"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=input_output,
            adapter_factories=_factories(config, trace),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    assert trace.factory_calls == []
    assert not input_output.exists()
    input_failure = json.loads(
        (tmp_path / "input-output.failure.json").read_text(encoding="utf-8")
    )
    assert input_failure["stage"] == "input_validation"
    assert input_failure["model_id"] is None
    assert input_failure["batch_id"] is None


def test_harness_records_cuda_memory_with_synchronization_and_never_cpu_zeroes(
    tmp_path: Path,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        batch_size=2,
        warmup_batches=0,
        timing_repeats=1,
        device="cuda",
    )
    trace = _Trace()
    snapshots = [
        api["CudaMemorySnapshot"](allocated_bytes=101, reserved_bytes=202),
        api["CudaMemorySnapshot"](allocated_bytes=303, reserved_bytes=404),
    ]
    environment = api["BenchmarkExecutionEnvironment"](
        actual_device="cuda:0",
        actual_precision="float32",
        software={"runtime": "pytest"},
        hardware={"gpu": "fake-cuda"},
        memory_measurement_supported=True,
        synchronize=lambda: trace.events.append("sync"),
        current_memory=lambda: snapshots[0],
        reset_peak_memory=lambda: trace.events.append("reset_peak"),
        peak_memory=lambda: snapshots[1],
    )
    output_directory = tmp_path / "cuda-results"
    api["run_detector_benchmark"](
        manifest=manifest,
        run_config=config,
        preflight_reports=_ready_reports(config),
        images_root=images_root,
        output_directory=output_directory,
        adapter_factories=_factories(config, trace),
        execution_environment=environment,
        clock_ns=_FakeClock(trace),
    )

    batch = _read_jsonl(output_directory / "batches.jsonl")[0]
    assert trace.events.count("sync") == 2
    assert trace.events.count("reset_peak") == 1
    assert batch["memory_measurement_supported"] is True
    assert batch["cuda_memory"] == {
        "baseline_allocated_bytes": 101,
        "baseline_reserved_bytes": 202,
        "peak_allocated_bytes": 303,
        "peak_reserved_bytes": 404,
    }


def test_harness_score_hash_excludes_batch_timing_and_runtime_metadata(tmp_path: Path) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        batch_size=2,
        warmup_batches=0,
        timing_repeats=1,
    )
    first_trace = _Trace()
    second_trace = _Trace()
    first_output = tmp_path / "first-results"
    second_output = tmp_path / "second-results"
    common = {
        "manifest": manifest,
        "run_config": config,
        "preflight_reports": _ready_reports(config),
        "images_root": images_root,
    }
    api["run_detector_benchmark"](
        **common,
        output_directory=first_output,
        adapter_factories=_factories(config, first_trace),
        execution_environment=_cpu_environment(api, first_trace, label="cpu-a"),
        clock_ns=_FakeClock(first_trace, step_ns=1_000_000),
    )
    api["run_detector_benchmark"](
        **common,
        output_directory=second_output,
        adapter_factories=_factories(config, second_trace),
        execution_environment=_cpu_environment(api, second_trace, label="cpu-b"),
        clock_ns=_FakeClock(second_trace, step_ns=5_000_000),
    )

    first_run = json.loads((first_output / "run.json").read_text(encoding="utf-8"))
    second_run = json.loads((second_output / "run.json").read_text(encoding="utf-8"))
    first_batch = _read_jsonl(first_output / "batches.jsonl")[0]
    second_batch = _read_jsonl(second_output / "batches.jsonl")[0]
    assert first_run["canonical_score_sha256"] == second_run["canonical_score_sha256"]
    assert first_run["hardware"] != second_run["hardware"]
    assert first_batch["end_to_end_duration_ms"] != second_batch["end_to_end_duration_ms"]


def test_harness_rejects_existing_target_without_a_contradictory_failure(
    tmp_path: Path,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        warmup_batches=0,
        timing_repeats=1,
    )
    trace = _Trace()
    existing = tmp_path / "existing-results"
    existing.mkdir()
    (existing / "sentinel.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=existing,
            adapter_factories=_factories(config, trace),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )
    assert (existing / "sentinel.txt").read_text(encoding="utf-8") == "preserve"
    assert trace.factory_calls == []
    assert not (tmp_path / "existing-results.failure.json").exists()

    failing_output = tmp_path / "failing-results"
    with pytest.raises(RuntimeError, match="synthetic forward failure"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=failing_output,
            adapter_factories=_factories(
                config,
                trace,
                failing_model_id="universal_fake_detector_head",
                fail_forward_at=2,
            ),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    assert not failing_output.exists()
    assert not list(tmp_path.glob(".failing-results-*"))
    failure = json.loads(
        (tmp_path / "failing-results.failure.json").read_text(encoding="utf-8")
    )
    assert failure["stage"] == "execution"
    assert failure["model_id"] == "universal_fake_detector_head"
    assert failure["batch_id"].endswith("batch-001")
    assert failure["exception_type"] == "RuntimeError"
    assert failure["completed_score_count"] == 1
    assert failure["completed_batch_count"] == 1


def test_harness_closes_partially_loaded_adapter_without_masking_the_load_error(
    tmp_path: Path,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        warmup_batches=0,
        timing_repeats=1,
    )
    trace = _Trace()
    output_directory = tmp_path / "partial-load-results"

    with pytest.raises(RuntimeError, match="synthetic partial load failure"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=output_directory,
            adapter_factories=_factories(
                config,
                trace,
                fail_load_after_allocation_model_id="universal_fake_detector_head",
                fail_close_model_id="universal_fake_detector_head",
            ),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    assert trace.events == [
        "load:universal_fake_detector_head:cpu:float32",
        "partial-allocation:universal_fake_detector_head",
        "close:universal_fake_detector_head",
    ]
    assert trace.active_model_id is None
    failure = json.loads(
        (tmp_path / "partial-load-results.failure.json").read_text(encoding="utf-8")
    )
    assert failure["stage"] == "adapter_load"
    assert failure["exception_type"] == "RuntimeError"


@pytest.mark.parametrize(
    ("failure_mode", "expected_stage", "exception_type", "message"),
    (
        ("forward", "execution", RuntimeError, "synthetic forward failure"),
        ("output_validation", "output_validation", ValueError, "output keys"),
    ),
)
def test_harness_close_failure_does_not_mask_inference_or_validation_errors(
    tmp_path: Path,
    failure_mode: str,
    expected_stage: str,
    exception_type: type[Exception],
    message: str,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        warmup_batches=0,
        timing_repeats=1,
    )
    trace = _Trace()
    output_directory = tmp_path / f"{failure_mode}-results"
    factory_kwargs: dict[str, Any] = {
        "fail_close_model_id": "universal_fake_detector_head",
    }
    if failure_mode == "forward":
        factory_kwargs.update(
            failing_model_id="universal_fake_detector_head",
            fail_forward_at=1,
        )
    else:
        factory_kwargs["invalid_outputs_model_id"] = "universal_fake_detector_head"

    with pytest.raises(exception_type, match=message):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=output_directory,
            adapter_factories=_factories(config, trace, **factory_kwargs),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    assert trace.events.count("close:universal_fake_detector_head") == 1
    failure = json.loads(
        (tmp_path / f"{failure_mode}-results.failure.json").read_text(encoding="utf-8")
    )
    assert failure["stage"] == expected_stage
    assert failure["exception_type"] == exception_type.__name__


@pytest.mark.parametrize(
    "bad_model_id",
    (
        "universal_fake_detector_head",
        "commfor_model_384",
        "watermark_siglip2",
        "wd14_eva02_large_v3",
    ),
)
def test_harness_rejects_each_detector_model_provenance_hash_mismatch_before_publication(
    tmp_path: Path,
    bad_model_id: str,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(manifest, warmup_batches=0, timing_repeats=1)
    trace = _Trace()
    output_directory = tmp_path / f"bad-provenance-{bad_model_id}"

    with pytest.raises(ValueError, match="model artifact SHA-256"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=output_directory,
            adapter_factories=_factories(
                config,
                trace,
                provenance_sha256_by_model_id={bad_model_id: "f" * 64},
            ),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    assert not output_directory.exists()
    assert trace.factory_calls == [model.model_id for model in config.models]
    failure = json.loads(
        (tmp_path / f"bad-provenance-{bad_model_id}.failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["stage"] == "output_validation"
    assert failure["model_id"] is None
    assert failure["batch_id"] is None


def test_harness_refuses_an_existing_failure_sidecar_without_overwriting_or_publishing_success(
    tmp_path: Path,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        warmup_batches=0,
        timing_repeats=1,
    )
    trace = _Trace()
    output_directory = tmp_path / "existing-failure-results"
    failure_path = tmp_path / "existing-failure-results.failure.json"
    original_failure = '{"old_failure":true}\n'
    failure_path.write_text(original_failure, encoding="utf-8")

    with pytest.raises(FileExistsError, match="failure sidecar already exists"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=output_directory,
            adapter_factories=_factories(config, trace),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    assert trace.factory_calls == []
    assert not output_directory.exists()
    assert failure_path.read_text(encoding="utf-8") == original_failure


def test_harness_marks_warmup_decode_failure_as_execution_without_a_stale_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head", "commfor_model_384"),
        batch_size=2,
        warmup_batches=1,
        timing_repeats=1,
    )
    trace = _Trace()
    output_directory = tmp_path / "warmup-decode-results"
    original_decode = api["module"]._decode_batch_images
    decode_count = 0

    def fail_second_model_warmup(paths: Sequence[Path]) -> list[Image.Image]:
        nonlocal decode_count
        decode_count += 1
        if decode_count == 3:
            raise OSError("synthetic warmup decode failure")
        return original_decode(paths)

    monkeypatch.setattr(api["module"], "_decode_batch_images", fail_second_model_warmup)
    with pytest.raises(OSError, match="synthetic warmup decode failure"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=output_directory,
            adapter_factories=_factories(config, trace),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    failure = json.loads(
        (tmp_path / "warmup-decode-results.failure.json").read_text(encoding="utf-8")
    )
    assert failure["stage"] == "execution"
    assert failure["model_id"] == "commfor_model_384"
    assert failure["batch_id"] is None


def test_harness_marks_timed_decode_failure_as_execution_with_its_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        batch_size=2,
        warmup_batches=0,
        timing_repeats=1,
    )
    trace = _Trace()
    output_directory = tmp_path / "timed-decode-results"

    def fail_decode(_paths: Sequence[Path]) -> list[Image.Image]:
        raise OSError("synthetic timed decode failure")

    monkeypatch.setattr(api["module"], "_decode_batch_images", fail_decode)
    with pytest.raises(OSError, match="synthetic timed decode failure"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=output_directory,
            adapter_factories=_factories(config, trace),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    failure = json.loads(
        (tmp_path / "timed-decode-results.failure.json").read_text(encoding="utf-8")
    )
    assert failure["stage"] == "execution"
    assert failure["model_id"] == "universal_fake_detector_head"
    assert failure["batch_id"].endswith("repeat-000-batch-000")


@pytest.mark.parametrize(
    ("patched_name", "exception_type"),
    (
        ("validate_sidecar_outputs", ValueError),
        ("_write_success_sidecars", OSError),
    ),
)
def test_harness_clears_model_context_for_global_validation_and_publication_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_name: str,
    exception_type: type[Exception],
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        warmup_batches=0,
        timing_repeats=1,
    )
    trace = _Trace()
    output_directory = tmp_path / f"global-{patched_name}-results"

    def fail_global_operation(*_args: Any, **_kwargs: Any) -> None:
        raise exception_type(f"synthetic {patched_name} failure")

    monkeypatch.setattr(api["module"], patched_name, fail_global_operation)
    with pytest.raises(exception_type, match=f"synthetic {patched_name} failure"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=config,
            preflight_reports=_ready_reports(config),
            images_root=images_root,
            output_directory=output_directory,
            adapter_factories=_factories(config, trace),
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )

    failure = json.loads(
        (tmp_path / f"global-{patched_name}-results.failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["stage"] == (
        "output_validation" if patched_name == "validate_sidecar_outputs" else "publication"
    )
    assert failure["model_id"] is None
    assert failure["batch_id"] is None


def test_harness_rejects_v1_without_constructing_an_adapter(tmp_path: Path) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    v2 = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        warmup_batches=0,
        timing_repeats=1,
    )
    v1_payload = v2.model_dump(mode="python")
    v1_payload["schema_version"] = "detector-benchmark-run/v1"
    v1_payload.pop("warmup_batches")
    v1_payload.pop("timing_repeats")
    v1_payload.pop("auxiliary_ocr")
    v1_payload["models"] = [v2.models[0].model_dump(mode="python")]
    v1_payload["models"][0].pop("batch_size")
    v1 = BenchmarkRunConfig.model_validate(v1_payload)
    trace = _Trace()
    with pytest.raises(ValueError, match="v2"):
        api["run_detector_benchmark"](
            manifest=manifest,
            run_config=v1,
            preflight_reports=(),
            images_root=images_root,
            output_directory=tmp_path / "v1-results",
            adapter_factories={},
            execution_environment=_cpu_environment(api, trace),
            clock_ns=_FakeClock(trace),
        )
    assert trace.factory_calls == []
