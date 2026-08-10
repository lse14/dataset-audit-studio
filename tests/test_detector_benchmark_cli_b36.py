from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import torch
from dataset_audit_studio.benchmarks.detector_preflight import (
    DetectorPreflightReport,
    PreflightFileResult,
)
from dataset_audit_studio.benchmarks.harness import BenchmarkExecutionEnvironment
from dataset_audit_studio.benchmarks.manifest import BenchmarkManifest, BenchmarkManifestEntry
from dataset_audit_studio.benchmarks.run_config import (
    OCR_DETECTOR_MODEL_ID,
    OCR_RECOGNIZER_MODEL_ID,
)
from dataset_audit_studio.benchmarks.sidecar import WD14_TAG_SCORE_LABELS
from PIL import Image

DETECTOR_MODEL_IDS = (
    "universal_fake_detector_head",
    "commfor_model_384",
    "watermark_siglip2",
    "wd14_eva02_large_v3",
)


def _api() -> dict[str, Any]:
    try:
        from dataset_audit_studio.benchmarks.detectors import (
            BenchmarkCliDependencies,
            main,
        )
    except ImportError as error:
        pytest.fail(f"B3.6 benchmark CLI API is not implemented: {error}")
    return {
        "BenchmarkCliDependencies": BenchmarkCliDependencies,
        "main": main,
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


MODEL_SHA256 = {model_id: _sha256(f"{model_id}:model") for model_id in DETECTOR_MODEL_IDS}
MODEL_SHA256[OCR_DETECTOR_MODEL_ID] = _sha256(f"{OCR_DETECTOR_MODEL_ID}:model")
MODEL_SHA256[OCR_RECOGNIZER_MODEL_ID] = _sha256(f"{OCR_RECOGNIZER_MODEL_ID}:model")
CLIP_SHA256 = _sha256("openai_clip_vit_l14:dependency")


def _annotation(value: str) -> dict[str, str]:
    return {
        "value": value,
        "label_source": "tests:fake-b36",
        "label_trust": "trusted",
    }


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (5, 3), color) as image:
        image.save(path)


def _manifest_entry(
    *, sample_id: str, image_path: str, image_sha256: str
) -> BenchmarkManifestEntry:
    return BenchmarkManifestEntry.model_validate(
        {
            "schema_version": "detector-benchmark-manifest/v1",
            "sample_id": sample_id,
            "image_path": image_path,
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


def _write_inputs(
    tmp_path: Path,
    *,
    version: str = "detector-benchmark-run/v2",
) -> tuple[Path, Path, Path, BenchmarkManifest]:
    images_root = tmp_path / "fake-images"
    first_image = images_root / "nested" / "sample-a.png"
    second_image = images_root / "nested" / "sample-b.png"
    _write_image(first_image, (10, 20, 30))
    _write_image(second_image, (30, 20, 10))
    manifest = BenchmarkManifest(
        entries=(
            _manifest_entry(
                sample_id="sample-b",
                image_path="nested/sample-b.png",
                image_sha256=hashlib.sha256(second_image.read_bytes()).hexdigest(),
            ),
            _manifest_entry(
                sample_id="sample-a",
                image_path="nested/sample-a.png",
                image_sha256=hashlib.sha256(first_image.read_bytes()).hexdigest(),
            ),
        )
    )
    manifest_path = tmp_path / "fake-manifest.jsonl"
    manifest_path.write_text(
        "".join(
            json.dumps(
                entry.model_dump(mode="json"),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for entry in manifest.entries
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "fake-run-config.json"
    model_references = [
        {
            "model_id": model_id,
            "role": "baseline" if index == 0 else "candidate",
            "source_kind": "huggingface",
            "source_repository": "tests/FakeOfflineAdapter",
            "revision": "a" * 40,
            "artifact_sha256": [MODEL_SHA256[model_id]],
            "declared_license": "MIT",
            "remote_code_allowed": False,
            **({"batch_size": 1} if version.endswith("/v2") else {}),
        }
        for index, model_id in enumerate(DETECTOR_MODEL_IDS)
    ]
    payload: dict[str, Any] = {
        "schema_version": version,
        "manifest_sha256": manifest.canonical_sha256,
        "seed": 17,
        "review_top_k": 5,
        "device": "cpu",
        "precision": "float32",
        "offline": True,
        "report_only": True,
        "models": model_references,
    }
    if version.endswith("/v2"):
        payload.update(
            {
                "warmup_batches": 1,
                "timing_repeats": 1,
                "auxiliary_ocr": {
                    "detector": {
                        "model_id": OCR_DETECTOR_MODEL_ID,
                        "source_kind": "huggingface",
                        "source_repository": "tests/FakeOcrDetector",
                        "revision": "b" * 40,
                        "artifact_sha256": [MODEL_SHA256[OCR_DETECTOR_MODEL_ID]],
                        "declared_license": "MIT",
                        "remote_code_allowed": False,
                        "batch_size": 1,
                    },
                    "recognizer": {
                        "model_id": OCR_RECOGNIZER_MODEL_ID,
                        "source_kind": "huggingface",
                        "source_repository": "tests/FakeOcrRecognizer",
                        "revision": "c" * 40,
                        "artifact_sha256": [MODEL_SHA256[OCR_RECOGNIZER_MODEL_ID]],
                        "declared_license": "MIT",
                        "remote_code_allowed": False,
                        "batch_size": 1,
                    },
                },
            }
        )
    config_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "reports" / "fake-run"
    output_path.parent.mkdir()
    return manifest_path, config_path, images_root, manifest


def _argv(
    manifest_path: Path,
    config_path: Path,
    images_root: Path,
    output_path: Path,
) -> list[str]:
    return [
        "run",
        "--manifest",
        str(manifest_path),
        "--run-config",
        str(config_path),
        "--images-root",
        str(images_root),
        "--output",
        str(output_path),
        "--offline",
    ]


def _report(model_id: str) -> DetectorPreflightReport:
    model_sha256 = MODEL_SHA256[model_id]
    return DetectorPreflightReport(
        model_id=model_id,
        status="ready",
        root=Path("."),
        files=(
            PreflightFileResult(
                path="fake-model.bin",
                status="ready",
                expected_size=17,
                actual_size=17,
                expected_sha256=model_sha256,
                actual_sha256=model_sha256,
            ),
        ),
        errors=(),
        run_config_artifacts="matched",
    )


@dataclass
class _Trace:
    preflight_model_ids: list[tuple[str, ...]] = field(default_factory=list)
    constructed_model_ids: list[str] = field(default_factory=list)
    loaded_model_ids: list[str] = field(default_factory=list)
    forwarded_model_ids: list[str] = field(default_factory=list)
    closed_model_ids: list[str] = field(default_factory=list)
    ocr_loads: int = 0
    ocr_forwards: int = 0
    ocr_closes: int = 0


class _FakeDetectorAdapter:
    def __init__(
        self,
        *,
        model_id: str,
        trace: _Trace,
        fail_load: bool = False,
        nonfinite_output: bool = False,
    ) -> None:
        self.model_id = model_id
        self._trace = trace
        self._fail_load = fail_load
        self._nonfinite_output = nonfinite_output
        self._loaded = False
        self._closed = False

    @property
    def provenance(self) -> dict[str, Any]:
        assets: list[dict[str, Any]] = [
            {
                "asset_id": self.model_id,
                "role": "model",
                "files": [
                    {
                        "path": "fake-model.bin",
                        "sha256": MODEL_SHA256[self.model_id],
                        "size_bytes": 17,
                    }
                ],
            }
        ]
        if self.model_id == "universal_fake_detector_head":
            assets.append(
                {
                    "asset_id": "openai_clip_vit_l14",
                    "role": "dependency",
                    "files": [
                        {
                            "path": "fake-clip.bin",
                            "sha256": CLIP_SHA256,
                            "size_bytes": 19,
                        }
                    ],
                }
            )
        return {
            "model_id": self.model_id,
            "assets": assets,
            "preprocessing": {
                "source": "tests.fake_b36",
                "revision": "fake-v1",
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
        assert preflight_report.status == "ready"
        assert preflight_report.run_config_artifacts == "matched"
        assert requested_device == "cpu"
        assert requested_precision == "float32"
        self._trace.loaded_model_ids.append(self.model_id)
        self._loaded = True
        if self._fail_load:
            raise RuntimeError("synthetic B3.6 load failure")

    def preprocess(self, images: Sequence[Image.Image]) -> torch.Tensor:
        assert self._loaded
        return torch.zeros((len(images), 1), dtype=torch.float32)

    def transfer_to_device(
        self,
        prepared: torch.Tensor,
        *,
        device: str,
        precision: str,
    ) -> torch.Tensor:
        assert device == "cpu"
        assert precision == "float32"
        return prepared

    def forward(self, transferred: torch.Tensor) -> list[dict[str, Any]]:
        assert self._loaded
        self._trace.forwarded_model_ids.append(self.model_id)
        if self._nonfinite_output:
            return [{"raw_sigmoid_score": float("nan")} for _ in range(len(transferred))]
        return [_raw_output(self.model_id) for _ in range(len(transferred))]

    def to_cpu(self, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return outputs

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._trace.closed_model_ids.append(self.model_id)


class _FakeOcrAdapter:
    model_id = OCR_DETECTOR_MODEL_ID

    def __init__(self, trace: _Trace) -> None:
        self._trace = trace
        self._loaded = False
        self._closed = False

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "assets": [
                {
                    "asset_id": OCR_DETECTOR_MODEL_ID,
                    "role": "model",
                    "files": [
                        {
                            "path": "fake-detector.bin",
                            "sha256": MODEL_SHA256[OCR_DETECTOR_MODEL_ID],
                            "size_bytes": 23,
                        }
                    ],
                },
                {
                    "asset_id": OCR_RECOGNIZER_MODEL_ID,
                    "role": "model",
                    "files": [
                        {
                            "path": "fake-recognizer.bin",
                            "sha256": MODEL_SHA256[OCR_RECOGNIZER_MODEL_ID],
                            "size_bytes": 29,
                        }
                    ],
                },
            ],
            "preprocessing": {
                "source": "tests.fake_b36_ocr",
                "revision": "fake-v1",
            },
        }

    def load(
        self,
        *,
        detector_preflight_report: DetectorPreflightReport,
        recognizer_preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: str,
        recognizer_batch_size: int,
    ) -> None:
        assert detector_preflight_report.model_id == OCR_DETECTOR_MODEL_ID
        assert recognizer_preflight_report.model_id == OCR_RECOGNIZER_MODEL_ID
        assert requested_device == "cpu"
        assert requested_precision == "float32"
        assert recognizer_batch_size == 1
        self._loaded = True
        self._trace.ocr_loads += 1

    def preprocess(self, images: Sequence[Image.Image]) -> torch.Tensor:
        assert self._loaded
        return torch.zeros((len(images), 1), dtype=torch.float32)

    def transfer_to_device(
        self,
        prepared: torch.Tensor,
        *,
        device: str,
        precision: str,
    ) -> torch.Tensor:
        assert device == "cpu"
        assert precision == "float32"
        return prepared

    def forward(self, transferred: torch.Tensor) -> list[dict[str, Any]]:
        assert self._loaded
        self._trace.ocr_forwards += 1
        return [
            {
                "regions": [
                    {
                        "box": [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]],
                        "detection_score": 0.8,
                        "recognition_score": 0.7,
                        "text": f"fake-{index}",
                    }
                ],
                "text_area_ratio": 0.2,
            }
            for index in range(len(transferred))
        ]

    def to_cpu(self, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return outputs

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._trace.ocr_closes += 1


def _raw_output(model_id: str) -> dict[str, Any]:
    if model_id in {"universal_fake_detector_head", "commfor_model_384"}:
        return {"raw_sigmoid_score": 0.25}
    if model_id == "watermark_siglip2":
        return {
            "raw_softmax_label_score": 0.75,
            "raw_softmax_label_scores": {"Clean": 0.25, "Watermark": 0.75},
        }
    if model_id == "wd14_eva02_large_v3":
        return {"raw_sigmoid_tag_scores": {tag: 0.5 for tag in WD14_TAG_SCORE_LABELS}}
    raise AssertionError(f"unexpected fake detector: {model_id}")


class _FakeClock:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> int:
        self._value += 1_000_000
        return self._value


def _dependencies(
    api: Mapping[str, Any],
    trace: _Trace,
    *,
    fail_load_for: str | None = None,
    nonfinite_output_for: str | None = None,
) -> Any:
    def preflight_reports(run_config: Any) -> tuple[DetectorPreflightReport, ...]:
        trace.preflight_model_ids.append(
            tuple(model.model_id for model in run_config.models)
        )
        reports = [_report(model.model_id) for model in run_config.models]
        if run_config.auxiliary_ocr is not None:
            reports.extend(
                (
                    _report(run_config.auxiliary_ocr.detector.model_id),
                    _report(run_config.auxiliary_ocr.recognizer.model_id),
                )
            )
        return tuple(reports)

    def factory(model_id: str) -> _FakeDetectorAdapter:
        def construct() -> _FakeDetectorAdapter:
            trace.constructed_model_ids.append(model_id)
            return _FakeDetectorAdapter(
                model_id=model_id,
                trace=trace,
                fail_load=(model_id == fail_load_for),
                nonfinite_output=(model_id == nonfinite_output_for),
            )

        return construct

    environment = BenchmarkExecutionEnvironment(
        actual_device="cpu",
        actual_precision="float32",
        software={"runtime": "fake-b36"},
        hardware={"host": "fake-b36"},
        memory_measurement_supported=False,
    )
    return api["BenchmarkCliDependencies"](
        preflight_reports=preflight_reports,
        adapter_factories={model_id: factory(model_id) for model_id in DETECTOR_MODEL_IDS},
        ocr_adapter_factory=lambda: _FakeOcrAdapter(trace),
        execution_environment=environment,
        clock_ns=_FakeClock(),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _install_offline_failure_hooks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    blocked_calls: list[str] = []

    def blocked(name: str) -> Any:
        def fail(*_args: Any, **_kwargs: Any) -> None:
            blocked_calls.append(name)
            raise AssertionError(f"B3.6 fake E2E attempted forbidden {name}")

        return fail

    monkeypatch.setattr(socket, "create_connection", blocked("socket.create_connection"))
    monkeypatch.setattr(socket.socket, "connect", blocked("socket.socket.connect"))
    monkeypatch.setattr(torch.hub, "load", blocked("torch.hub.load"))
    from dataset_audit_studio.benchmarks import detectors
    from dataset_audit_studio.model_adapters.downloads import ModelDownloadManager

    monkeypatch.setattr(
        detectors,
        "build_default_dependencies",
        blocked("default benchmark dependencies"),
    )
    monkeypatch.setattr(ModelDownloadManager, "start_download", blocked("model download"))
    return blocked_calls


def test_b36_cli_runs_all_fake_adapters_atomically_and_keeps_ocr_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    manifest_path, config_path, images_root, _manifest = _write_inputs(tmp_path)
    output_path = tmp_path / "reports" / "fake-run"
    trace = _Trace()
    blocked_calls = _install_offline_failure_hooks(monkeypatch)

    exit_code = api["main"](
        _argv(manifest_path, config_path, images_root, output_path),
        dependencies=_dependencies(api, trace),
    )

    assert exit_code == 0
    assert blocked_calls == []
    assert trace.preflight_model_ids == [DETECTOR_MODEL_IDS]
    assert trace.constructed_model_ids == list(DETECTOR_MODEL_IDS)
    assert trace.loaded_model_ids == list(DETECTOR_MODEL_IDS)
    assert trace.closed_model_ids == list(DETECTOR_MODEL_IDS)
    assert trace.ocr_loads == 1
    assert trace.ocr_forwards == 3  # One warmup and two timed one-image batches.
    assert trace.ocr_closes == 1
    assert {path.name for path in output_path.iterdir()} == {
        "batches.jsonl",
        "ocr.jsonl",
        "run.json",
        "scores.jsonl",
    }
    assert not output_path.with_name("fake-run.failure.json").exists()
    assert not list(output_path.parent.glob(".fake-run-*"))

    run = json.loads((output_path / "run.json").read_text(encoding="utf-8"))
    assert tuple(run["detector_model_ids"]) == DETECTOR_MODEL_IDS
    assert tuple(item["model_id"] for item in run["detector_provenance"]) == DETECTOR_MODEL_IDS
    for provenance in run["detector_provenance"]:
        model_asset = next(asset for asset in provenance["assets"] if asset["role"] == "model")
        assert model_asset["files"][0]["sha256"] == MODEL_SHA256[provenance["model_id"]]
    ufd_provenance = run["detector_provenance"][0]
    assert {(asset["asset_id"], asset["role"]) for asset in ufd_provenance["assets"]} == {
        ("universal_fake_detector_head", "model"),
        ("openai_clip_vit_l14", "dependency"),
    }
    assert run["auxiliary_ocr_enabled"] is True
    assert [asset["asset_id"] for asset in run["auxiliary_ocr_provenance"]["assets"]] == [
        OCR_DETECTOR_MODEL_ID,
        OCR_RECOGNIZER_MODEL_ID,
    ]

    scores = _read_jsonl(output_path / "scores.jsonl")
    assert {score["model_id"] for score in scores} == set(DETECTOR_MODEL_IDS)
    assert all("ppocr" not in score["model_id"] for score in scores)
    ocr_records = _read_jsonl(output_path / "ocr.jsonl")
    assert len(ocr_records) == 2
    assert {record["detector_model_id"] for record in ocr_records} == {OCR_DETECTOR_MODEL_ID}
    assert {record["recognizer_model_id"] for record in ocr_records} == {OCR_RECOGNIZER_MODEL_ID}


def test_b36_cli_returns_one_and_failure_sidecar_for_fake_load_failure(tmp_path: Path) -> None:
    api = _api()
    manifest_path, config_path, images_root, _manifest = _write_inputs(tmp_path)
    output_path = tmp_path / "reports" / "fake-run"
    trace = _Trace()

    exit_code = api["main"](
        _argv(manifest_path, config_path, images_root, output_path),
        dependencies=_dependencies(
            api,
            trace,
            fail_load_for="universal_fake_detector_head",
        ),
    )

    assert exit_code == 1
    assert not output_path.exists()
    assert not (output_path / "scores.jsonl").exists()
    failure = json.loads(
        output_path.with_name("fake-run.failure.json").read_text(encoding="utf-8")
    )
    assert failure["stage"] == "adapter_load"
    assert failure["model_id"] == "universal_fake_detector_head"
    assert failure["completed_score_count"] == 0
    assert failure["completed_batch_count"] == 0
    assert trace.constructed_model_ids == ["universal_fake_detector_head"]
    assert trace.closed_model_ids == ["universal_fake_detector_head"]
    assert not list(output_path.parent.glob(".fake-run-*"))


def test_b36_cli_returns_one_without_success_output_for_nonfinite_fake_score(
    tmp_path: Path,
) -> None:
    api = _api()
    manifest_path, config_path, images_root, _manifest = _write_inputs(tmp_path)
    output_path = tmp_path / "reports" / "fake-run"
    trace = _Trace()

    exit_code = api["main"](
        _argv(manifest_path, config_path, images_root, output_path),
        dependencies=_dependencies(
            api,
            trace,
            nonfinite_output_for="commfor_model_384",
        ),
    )

    assert exit_code == 1
    assert not output_path.exists()
    failure = json.loads(
        output_path.with_name("fake-run.failure.json").read_text(encoding="utf-8")
    )
    assert failure["stage"] == "output_validation"
    assert failure["model_id"] == "commfor_model_384"
    assert failure["completed_score_count"] == 2
    assert failure["completed_batch_count"] == 2
    assert trace.closed_model_ids == ["universal_fake_detector_head", "commfor_model_384"]
    assert not list(output_path.parent.glob(".fake-run-*"))


def test_b36_cli_rejects_v1_through_harness_with_exit_one(tmp_path: Path) -> None:
    api = _api()
    manifest_path, config_path, images_root, _manifest = _write_inputs(
        tmp_path,
        version="detector-benchmark-run/v1",
    )
    output_path = tmp_path / "reports" / "fake-run"
    trace = _Trace()

    exit_code = api["main"](
        _argv(manifest_path, config_path, images_root, output_path),
        dependencies=_dependencies(api, trace),
    )

    assert exit_code == 1
    assert trace.preflight_model_ids == []
    assert trace.constructed_model_ids == []
    assert not output_path.exists()
    failure = json.loads(
        output_path.with_name("fake-run.failure.json").read_text(encoding="utf-8")
    )
    assert failure["stage"] == "preflight"
    assert failure["exception_type"] == "ValueError"


def test_b361_module_entry_propagates_parsed_run_failure_exit_one(tmp_path: Path) -> None:
    workspace_root = Path(__file__).resolve().parents[1]
    missing_manifest = tmp_path / "missing-manifest.jsonl"
    output_path = tmp_path / "reports" / "fake-run"
    environment = os.environ.copy()
    backend_path = str(workspace_root / "backend")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        backend_path
        if not existing_pythonpath
        else os.pathsep.join((backend_path, existing_pythonpath))
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "dataset_audit_studio.benchmarks.detectors",
            "run",
            "--manifest",
            str(missing_manifest),
            "--run-config",
            str(tmp_path / "missing-run-config.json"),
            "--images-root",
            str(tmp_path / "fake-images"),
            "--output",
            str(output_path),
            "--offline",
        ],
        cwd=workspace_root,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 1
    assert "detector benchmark failed:" in completed.stderr
    assert not output_path.exists()
    assert not output_path.with_name("fake-run.failure.json").exists()


def test_b36_cli_parser_returns_two_without_runtime_and_help_returns_zero(
    tmp_path: Path,
) -> None:
    api = _api()
    manifest_path, config_path, images_root, _manifest = _write_inputs(tmp_path)
    output_path = tmp_path / "reports" / "fake-run"
    trace = _Trace()
    dependencies = _dependencies(api, trace)

    with pytest.raises(SystemExit) as help_exit:
        api["main"](["--help"], dependencies=dependencies)
    assert help_exit.value.code == 0

    with pytest.raises(SystemExit) as missing_argument_exit:
        api["main"](
            ["run", "--manifest", str(manifest_path)],
            dependencies=dependencies,
        )
    assert missing_argument_exit.value.code == 2

    with pytest.raises(SystemExit) as unknown_argument_exit:
        api["main"](
            _argv(manifest_path, config_path, images_root, output_path) + ["--unknown"],
            dependencies=dependencies,
        )
    assert unknown_argument_exit.value.code == 2
    assert trace.preflight_model_ids == []
    assert trace.constructed_model_ids == []
    assert not output_path.exists()
    assert not output_path.with_name("fake-run.failure.json").exists()
