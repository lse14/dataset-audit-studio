from __future__ import annotations

import hashlib
import json
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import torch
from dataset_audit_studio.benchmarks.detector_preflight import (
    REQUIRED_WD14_TAGS,
    DetectorPreflightReport,
    PreflightFileResult,
)
from dataset_audit_studio.benchmarks.manifest import BenchmarkManifest, BenchmarkManifestEntry
from dataset_audit_studio.benchmarks.run_config import BenchmarkRunConfigV2
from dataset_audit_studio.benchmarks.sidecar import validate_detector_batch_outputs
from dataset_audit_studio.core.model_assets import AssetFile, ModelAsset, RuntimeAssets
from PIL import Image
from pydantic import ValidationError

WATERMARK_MODEL_ID = "watermark_siglip2"
WD14_MODEL_ID = "wd14_eva02_large_v3"
OCR_DETECTOR_MODEL_ID = "ppocrv5_server_det"
OCR_RECOGNIZER_MODEL_ID = "ppocrv5_server_rec"

WATERMARK_FILES = (
    ("config.json", 1_133, "ea97d917884a70ca1f543a0ea4a916b86704c69e13797246db130f73be26685b"),
    (
        "preprocessor_config.json",
        394,
        "9b36b57ebaf20f09bf4c22100ccc21877ea6bfe5aead0c00c59f8af8ccefacfc",
    ),
    (
        "model.safetensors",
        371_567_992,
        "c0cbfb77eb98b584a4f5d3aabe2ad6d96b546958cd2d9a0587f4c9793b4a42ff",
    ),
)
WD14_FILES = (
    ("config.json", 634, "1db05aefb1a245533818e33bea22852300ac647a64636854095d1a313cd2e9dc"),
    (
        "sw_jax_cv_config.json",
        469,
        "9b81a8f078c929a2dd213ef59d1b8862c79e9b48c49dfd38aac136de43474e19",
    ),
    (
        "selected_tags.csv",
        308_468,
        "298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217",
    ),
    (
        "model.safetensors",
        1_260_796_004,
        "74f05b0aad869d9f91fbc597bc8d157d98abdead573d5c23509a195dbb8a7ef5",
    ),
)
OCR_DETECTOR_FILES = (
    ("config.json", 1_591, "96a7ae464b59a9769aaf55d2b10e4dd8d3b64a7f3cbfa75bc2309febc6da2bcf"),
    (
        "inference.yml",
        903,
        "28fb721efc3634fc8aa677e474b9602cb815a91cf569ef357a7a553d7b3ce685",
    ),
    (
        "preprocessor_config.json",
        812,
        "e2afd5f1732ff4f096d02731990ab5a73070fce816e3cdb44d19849acbbe6525",
    ),
    (
        "model.safetensors",
        87_994_336,
        "06e7a44aa5c4146531e88703140e0f7329910b92f84f8bf2756bfd37cec5cb0a",
    ),
)
OCR_RECOGNIZER_FILES = (
    ("config.json", 768, "6e37472f191320b7da226b924ec282e5c5dbf4ac06f0f5ddf6d466ead60665c4"),
    (
        "inference.yml",
        148_345,
        "2c719dba044c4e2228aef8ff92f5f575394d75d24c16de096a33b7cfd902f66d",
    ),
    (
        "preprocessor_config.json",
        202_967,
        "2f9968f3f38a6fa0b94fd7342dcdbf3a8a53c20bb9e4ab23820a2170480381bf",
    ),
    (
        "model.safetensors",
        84_442_268,
        "da94c5dd42c88b00c44081942b157bee8eda9ccd0481dbd0af3824f6c241aa91",
    ),
)

def _api() -> dict[str, Any]:
    try:
        from dataset_audit_studio.benchmarks.detector_adapters import (
            PPOCRv5BenchmarkAdapter,
            WatermarkSiglip2BenchmarkAdapter,
            WD14TaggerBenchmarkAdapter,
            preprocess_wd14_image,
        )
        from dataset_audit_studio.benchmarks.harness import (
            BenchmarkExecutionEnvironment,
            run_detector_benchmark,
        )
        from dataset_audit_studio.benchmarks.sidecar import (
            BenchmarkSidecarAuxiliaryOcrProvenance,
            validate_ocr_batch_outputs,
        )
    except ImportError as error:
        pytest.fail(f"B3.5 benchmark adapter API is not implemented: {error}")
    return {
        "BenchmarkExecutionEnvironment": BenchmarkExecutionEnvironment,
        "BenchmarkSidecarAuxiliaryOcrProvenance": BenchmarkSidecarAuxiliaryOcrProvenance,
        "PPOCRv5BenchmarkAdapter": PPOCRv5BenchmarkAdapter,
        "WD14TaggerBenchmarkAdapter": WD14TaggerBenchmarkAdapter,
        "WatermarkSiglip2BenchmarkAdapter": WatermarkSiglip2BenchmarkAdapter,
        "preprocess_wd14_image": preprocess_wd14_image,
        "run_detector_benchmark": run_detector_benchmark,
        "validate_ocr_batch_outputs": validate_ocr_batch_outputs,
    }


@dataclass
class _Trace:
    events: list[str] = field(default_factory=list)
    watermark_batches: list[int] = field(default_factory=list)
    wd14_batches: list[int] = field(default_factory=list)
    ocr_batches: list[int] = field(default_factory=list)
    moved_devices: list[str] = field(default_factory=list)
    watermark_close_calls: int = 0
    wd14_close_calls: int = 0
    ocr_close_calls: int = 0


class _FakeBatch(dict[str, torch.Tensor]):
    def __init__(self, trace: _Trace, values: Mapping[str, torch.Tensor]) -> None:
        super().__init__(values)
        self._trace = trace

    def to(self, device: str) -> _FakeBatch:
        self._trace.events.append(f"watermark-transfer:{device}")
        return self


class _FakeWatermarkProcessor:
    def __init__(self, trace: _Trace) -> None:
        self._trace = trace

    def __call__(self, *, images: Sequence[Image.Image], return_tensors: str) -> _FakeBatch:
        assert return_tensors == "pt"
        assert all(image.mode == "RGB" for image in images)
        self._trace.events.append("watermark-preprocess")
        return _FakeBatch(
            self._trace,
            {"pixel_values": torch.zeros((len(images), 3, 2, 2), dtype=torch.float32)},
        )


class _FakeWatermarkModel:
    def __init__(self, trace: _Trace, *, logits: torch.Tensor | None = None) -> None:
        self._trace = trace
        self._logits = logits

    def __call__(self, **inputs: torch.Tensor) -> Any:
        pixels = inputs["pixel_values"]
        self._trace.events.append("watermark-forward")
        self._trace.watermark_batches.append(int(pixels.shape[0]))
        logits = self._logits
        if logits is None:
            logits = torch.tensor([[0.0, 1.0]], dtype=torch.float32).repeat(pixels.shape[0], 1)
        return type("Output", (), {"logits": logits})()


class _FakeWatermarkRuntime:
    def __init__(
        self,
        trace: _Trace,
        *,
        logits: torch.Tensor | None = None,
        labels: Mapping[int, str] | None = None,
    ) -> None:
        self._trace = trace
        self.processor: Any = _FakeWatermarkProcessor(trace)
        self.model: Any = _FakeWatermarkModel(trace, logits=logits)
        self.labels = dict(labels or {0: "No Watermark", 1: "Watermark"})
        self.watermark_index = 1

    def close(self) -> None:
        self._trace.watermark_close_calls += 1
        self.processor = None
        self.model = None


class _FakeWD14Model:
    def __init__(
        self,
        trace: _Trace,
        *,
        logits: torch.Tensor | None = None,
        fail_move: bool = False,
    ) -> None:
        self._trace = trace
        self._logits = logits
        self._fail_move = fail_move

    def to(self, device: str) -> _FakeWD14Model:
        self._trace.events.append("wd14-to")
        self._trace.moved_devices.append(str(device))
        if self._fail_move:
            raise RuntimeError("synthetic WD14 device move failure")
        return self

    def __call__(self, pixels: torch.Tensor) -> torch.Tensor:
        self._trace.events.append("wd14-forward")
        self._trace.wd14_batches.append(int(pixels.shape[0]))
        if self._logits is not None:
            return self._logits
        return torch.zeros((pixels.shape[0], len(REQUIRED_WD14_TAGS)), dtype=torch.float32)

    def close(self) -> None:
        self._trace.wd14_close_calls += 1


class _FakeWD14Loader:
    def __init__(
        self,
        trace: _Trace,
        *,
        logits: torch.Tensor | None = None,
        fail_move: bool = False,
    ) -> None:
        self._trace = trace
        self._logits = logits
        self._fail_move = fail_move
        self.report: DetectorPreflightReport | None = None

    def load(self, report: DetectorPreflightReport) -> Any:
        self.report = report
        self._trace.events.append("wd14-load")
        return type(
            "Loaded",
            (),
            {
                "model": _FakeWD14Model(
                    self._trace,
                    logits=self._logits,
                    fail_move=self._fail_move,
                ),
                "tags": tuple(sorted(REQUIRED_WD14_TAGS)),
            },
        )()


class _FakeOcrRuntime:
    def __init__(
        self,
        trace: _Trace,
        *,
        outputs: Sequence[Mapping[str, Any]] | None = None,
        score_callable: bool = True,
    ) -> None:
        self._trace = trace
        self._outputs = outputs
        self.score: Any = self._score if score_callable else None

    def _score(self, images: tuple[Image.Image, ...]) -> list[dict[str, Any]]:
        self._trace.events.append("ocr-forward")
        self._trace.ocr_batches.append(len(images))
        if self._outputs is not None:
            return [dict(item) for item in self._outputs]
        return [
            {
                "regions": [
                    {
                        "box": [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]],
                        "detection_score": 0.8,
                        "recognition_score": 0.7,
                        "text": f"region-{index}",
                    }
                ],
                "text_area_ratio": 0.1,
            }
            for index, _image in enumerate(images)
        ]

    def close(self) -> None:
        self._trace.ocr_close_calls += 1


def _asset(model_id: str, files: Sequence[tuple[str, int, str]]) -> ModelAsset:
    return ModelAsset(
        model_id=model_id,
        loader=f"{model_id}_loader",
        root=".",
        files=tuple(
            AssetFile(path=path, size=size, sha256=sha256, mtime_ns=1)
            for path, size, sha256 in files
        ),
        dependencies=(),
        is_custom=False,
        base_model_id=None,
    )


def _runtime_assets() -> RuntimeAssets:
    return RuntimeAssets(
        models_root=".",
        models=(
            _asset(WATERMARK_MODEL_ID, WATERMARK_FILES),
            _asset(WD14_MODEL_ID, WD14_FILES),
            _asset(OCR_DETECTOR_MODEL_ID, OCR_DETECTOR_FILES),
            _asset(OCR_RECOGNIZER_MODEL_ID, OCR_RECOGNIZER_FILES),
        ),
    )


def _report(
    model_id: str,
    files: Sequence[tuple[str, int, str]],
    *,
    status: str = "ready",
    artifact_status: str = "matched",
) -> DetectorPreflightReport:
    return DetectorPreflightReport(
        model_id=model_id,
        status=status,  # type: ignore[arg-type]
        root=Path("."),
        files=tuple(
            PreflightFileResult(
                path=path,
                status="ready",
                expected_size=size,
                actual_size=size,
                expected_sha256=sha256,
                actual_sha256=sha256,
            )
            for path, size, sha256 in files
        ),
        errors=(),
        run_config_artifacts=artifact_status,  # type: ignore[arg-type]
    )


def _watermark_adapter(
    api: dict[str, Any],
    trace: _Trace,
    *,
    logits: torch.Tensor | None = None,
    labels: Mapping[int, str] | None = None,
) -> Any:
    def build_runtime(*_args: Any, **_kwargs: Any) -> _FakeWatermarkRuntime:
        trace.events.append("watermark-load")
        return _FakeWatermarkRuntime(trace, logits=logits, labels=labels)

    return api["WatermarkSiglip2BenchmarkAdapter"](
        runtime_assets=_runtime_assets(),
        runtime_factory=build_runtime,
    )


def _wd14_adapter(
    api: dict[str, Any],
    trace: _Trace,
    *,
    logits: torch.Tensor | None = None,
    fail_move: bool = False,
    preprocess_image: Callable[[Image.Image], torch.Tensor] | None = None,
) -> tuple[Any, _FakeWD14Loader]:
    loader = _FakeWD14Loader(trace, logits=logits, fail_move=fail_move)
    adapter = api["WD14TaggerBenchmarkAdapter"](
        wd14_loader=loader,
        preprocess_image=preprocess_image,
    )
    return adapter, loader


def _ocr_adapter(
    api: dict[str, Any],
    trace: _Trace,
    *,
    outputs: Sequence[Mapping[str, Any]] | None = None,
    score_callable: bool = True,
) -> Any:
    def build_runtime(*_args: Any, **_kwargs: Any) -> _FakeOcrRuntime:
        trace.events.append("ocr-load")
        return _FakeOcrRuntime(trace, outputs=outputs, score_callable=score_callable)

    return api["PPOCRv5BenchmarkAdapter"](
        runtime_assets=_runtime_assets(),
        runtime_factory=build_runtime,
    )


def _load_detector(adapter: Any, report: DetectorPreflightReport, *, device: str = "cpu") -> None:
    adapter.load(
        preflight_report=report,
        requested_device=device,
        requested_precision="float32",
    )


def _load_ocr(
    adapter: Any,
    *,
    detector_report: DetectorPreflightReport | None = None,
    recognizer_report: DetectorPreflightReport | None = None,
    device: str = "cpu",
) -> None:
    adapter.load(
        detector_preflight_report=detector_report
        or _report(OCR_DETECTOR_MODEL_ID, OCR_DETECTOR_FILES),
        recognizer_preflight_report=recognizer_report
        or _report(OCR_RECOGNIZER_MODEL_ID, OCR_RECOGNIZER_FILES),
        requested_device=device,
        requested_precision="float32",
    )


def _run_batch(adapter: Any, images: Sequence[Image.Image]) -> list[Mapping[str, Any]]:
    prepared = adapter.preprocess(images)
    transferred = adapter.transfer_to_device(
        prepared,
        device="cpu",
        precision="float32",
    )
    return list(adapter.to_cpu(adapter.forward(transferred)))


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


def _manifest(tmp_path: Path) -> tuple[Path, BenchmarkManifest]:
    images_root = tmp_path / "images"
    entries: list[BenchmarkManifestEntry] = []
    for sample_id, color in (
        ("sample-001", (1, 2, 3)),
        ("sample-002", (4, 5, 6)),
        ("sample-003", (7, 8, 9)),
    ):
        path = images_root / f"{sample_id}.png"
        _write_image(path, color)
        entries.append(
            BenchmarkManifestEntry.model_validate(
                {
                    "schema_version": "detector-benchmark-manifest/v1",
                    "sample_id": sample_id,
                    "image_path": path.name,
                    "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
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
        )
    return images_root, BenchmarkManifest(entries=tuple(reversed(entries)))


def _reference(
    model_id: str,
    files: Sequence[tuple[str, int, str]],
    *,
    batch_size: int,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "source_kind": "huggingface",
        "source_repository": "Example/Offline",
        "revision": "a" * 40,
        "artifact_sha256": [sha256 for _path, _size, sha256 in files],
        "declared_license": "Apache-2.0",
        "remote_code_allowed": False,
        "batch_size": batch_size,
    }


def _config(manifest: BenchmarkManifest) -> BenchmarkRunConfigV2:
    watermark = _reference(WATERMARK_MODEL_ID, WATERMARK_FILES, batch_size=2)
    watermark["role"] = "baseline"
    wd14 = _reference(WD14_MODEL_ID, WD14_FILES, batch_size=2)
    wd14["role"] = "candidate"
    return BenchmarkRunConfigV2.model_validate(
        {
            "schema_version": "detector-benchmark-run/v2",
            "manifest_sha256": manifest.canonical_sha256,
            "seed": 7,
            "review_top_k": 1,
            "device": "cpu",
            "precision": "float32",
            "offline": True,
            "report_only": True,
            "warmup_batches": 1,
            "timing_repeats": 2,
            "models": [watermark, wd14],
            "auxiliary_ocr": {
                "detector": _reference(
                    OCR_DETECTOR_MODEL_ID,
                    OCR_DETECTOR_FILES,
                    batch_size=2,
                ),
                "recognizer": _reference(
                    OCR_RECOGNIZER_MODEL_ID,
                    OCR_RECOGNIZER_FILES,
                    batch_size=2,
                ),
            },
        }
    )


def _environment(api: dict[str, Any]) -> Any:
    return api["BenchmarkExecutionEnvironment"](
        actual_device="cpu",
        actual_precision="float32",
        software={"test": "b35"},
        hardware={"test": "fake"},
        memory_measurement_supported=False,
    )


def test_b35_harness_runs_independent_adapters_and_keeps_ocr_out_of_scores(
    tmp_path: Path,
) -> None:
    api = _api()
    trace = _Trace()
    watermark = _watermark_adapter(api, trace)
    wd14, _loader = _wd14_adapter(
        api,
        trace,
        preprocess_image=lambda _image: torch.zeros((3, 2, 2), dtype=torch.float32),
    )
    ocr = _ocr_adapter(api, trace)
    images_root, manifest = _manifest(tmp_path)
    config = _config(manifest)

    result = api["run_detector_benchmark"](
        manifest=manifest,
        run_config=config,
        preflight_reports=(
            _report(WATERMARK_MODEL_ID, WATERMARK_FILES),
            _report(WD14_MODEL_ID, WD14_FILES),
            _report(OCR_DETECTOR_MODEL_ID, OCR_DETECTOR_FILES),
            _report(OCR_RECOGNIZER_MODEL_ID, OCR_RECOGNIZER_FILES),
        ),
        images_root=images_root,
        output_directory=tmp_path / "sidecar",
        adapter_factories={
            WATERMARK_MODEL_ID: lambda: watermark,
            WD14_MODEL_ID: lambda: wd14,
        },
        ocr_adapter_factory=lambda: ocr,
        execution_environment=_environment(api),
    )

    expected_batches = [2, 2, 1, 2, 1]
    assert trace.watermark_batches == expected_batches
    assert trace.wd14_batches == expected_batches
    assert trace.ocr_batches == expected_batches
    score_lines = (result.output_directory / "scores.jsonl").read_text(encoding="utf-8")
    scores = [json.loads(line) for line in score_lines.splitlines()]
    assert {score["model_id"] for score in scores} == {WATERMARK_MODEL_ID, WD14_MODEL_ID}
    assert all("ppocr" not in score["model_id"] for score in scores)
    ocr_records = [
        json.loads(line)
        for line in (result.output_directory / "ocr.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(ocr_records) == 3
    assert all(record["detector_model_id"] == OCR_DETECTOR_MODEL_ID for record in ocr_records)
    assert all(record["recognizer_model_id"] == OCR_RECOGNIZER_MODEL_ID for record in ocr_records)
    assert result.run.auxiliary_ocr_enabled is True
    provenance = result.run.auxiliary_ocr_provenance
    assert provenance is not None
    assert [(asset.asset_id, asset.role) for asset in provenance.assets] == [
        (OCR_DETECTOR_MODEL_ID, "model"),
        (OCR_RECOGNIZER_MODEL_ID, "model"),
    ]
    assert provenance.assets[0].files[-1].size_bytes == 87_994_336
    assert provenance.assets[1].files[-1].size_bytes == 84_442_268
    assert trace.watermark_close_calls == 1
    assert trace.wd14_close_calls == 1
    assert trace.ocr_close_calls == 1


def test_b35_adapters_reject_nonready_or_unmatched_preflight_before_loading() -> None:
    api = _api()
    trace = _Trace()
    watermark = _watermark_adapter(api, trace)
    wd14, loader = _wd14_adapter(api, trace)
    ocr = _ocr_adapter(api, trace)

    with pytest.raises(RuntimeError, match="ready.*matched"):
        _load_detector(
            watermark,
            _report(WATERMARK_MODEL_ID, WATERMARK_FILES, status="missing"),
        )
    with pytest.raises(RuntimeError, match="ready.*matched"):
        _load_detector(
            wd14,
            _report(WD14_MODEL_ID, WD14_FILES, artifact_status="mismatch"),
        )
    with pytest.raises(RuntimeError, match="ready.*matched"):
        _load_ocr(
            ocr,
            recognizer_report=_report(
                OCR_RECOGNIZER_MODEL_ID,
                OCR_RECOGNIZER_FILES,
                artifact_status="mismatch",
            ),
        )

    assert trace.events == []
    assert loader.report is None


def test_watermark_adapter_uses_fixed_label_map_batch_softmax_and_auto_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    trace = _Trace()
    watermark = _watermark_adapter(api, trace)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    _load_detector(
        watermark,
        _report(WATERMARK_MODEL_ID, WATERMARK_FILES),
        device="auto",
    )
    outputs = _run_batch(
        watermark,
        [Image.new("RGB", (20, 10), "white"), Image.new("RGB", (10, 20), "black")],
    )

    assert "watermark-load" in trace.events
    assert "watermark-transfer:cpu" in trace.events
    assert outputs[0].keys() == {"raw_softmax_label_score", "raw_softmax_label_scores"}
    assert outputs[0]["raw_softmax_label_score"] == pytest.approx(0.73105858)
    assert outputs[0]["raw_softmax_label_scores"] == {
        "No Watermark": pytest.approx(0.26894142),
        "Watermark": pytest.approx(0.73105858),
    }
    asset = watermark.provenance.assets[0]
    assert asset.asset_id == WATERMARK_MODEL_ID
    assert asset.role == "model"
    assert [file.size_bytes for file in asset.files] == [1_133, 394, 371_567_992]


def test_wd14_preprocess_is_fixed_and_adapter_emits_only_approved_raw_sigmoid_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    pixels = api["preprocess_wd14_image"](Image.new("RGB", (896, 448), (255, 128, 0)))
    assert tuple(pixels.shape) == (3, 448, 448)
    assert torch.allclose(
        pixels,
        torch.tensor([1.0, (128.0 / 255.0 - 0.5) / 0.5, -1.0]).reshape(3, 1, 1).expand_as(
            pixels
        ),
        atol=1e-6,
    )

    trace = _Trace()
    adapter, loader = _wd14_adapter(api, trace)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    _load_detector(adapter, _report(WD14_MODEL_ID, WD14_FILES), device="auto")
    outputs = _run_batch(adapter, [Image.new("RGB", (448, 448), "white")])

    assert loader.report is not None
    assert trace.moved_devices == ["cpu"]
    assert set(outputs[0]) == {"raw_sigmoid_tag_scores"}
    assert set(outputs[0]["raw_sigmoid_tag_scores"]) == set(REQUIRED_WD14_TAGS)
    assert all(
        value == pytest.approx(0.5)
        for value in outputs[0]["raw_sigmoid_tag_scores"].values()
    )
    asset = adapter.provenance.assets[0]
    assert asset.asset_id == WD14_MODEL_ID
    assert asset.role == "model"
    assert asset.files[-1].size_bytes == 1_260_796_004


def test_ppocr_adapter_preserves_raw_regions_without_score_aggregation() -> None:
    api = _api()
    trace = _Trace()
    adapter = _ocr_adapter(api, trace)
    _load_ocr(adapter)
    outputs = _run_batch(
        adapter,
        [Image.new("RGB", (32, 16), "white"), Image.new("RGB", (16, 32), "black")],
    )

    assert trace.ocr_batches == [2]
    assert set(outputs[0]) == {"regions", "text_area_ratio"}
    assert outputs[0]["regions"][0]["detection_score"] == 0.8
    assert outputs[0]["regions"][0]["recognition_score"] == 0.7
    assert outputs[0]["regions"][0]["text"] == "region-0"
    records = api["validate_ocr_batch_outputs"](
        sample_ids=("sample-001", "sample-002"),
        outputs=outputs,
    )
    assert [record.sample_id for record in records] == ["sample-001", "sample-002"]
    assert all(record.detector_model_id == OCR_DETECTOR_MODEL_ID for record in records)
    assert all(record.recognizer_model_id == OCR_RECOGNIZER_MODEL_ID for record in records)
    provenance = adapter.provenance
    assert [(asset.asset_id, asset.role) for asset in provenance.assets] == [
        (OCR_DETECTOR_MODEL_ID, "model"),
        (OCR_RECOGNIZER_MODEL_ID, "model"),
    ]


def test_b35_output_shape_count_and_nonfinite_values_follow_existing_sidecar_rejection() -> None:
    api = _api()
    trace = _Trace()
    bad_watermark = _watermark_adapter(api, trace, logits=torch.zeros((2,), dtype=torch.float32))
    _load_detector(bad_watermark, _report(WATERMARK_MODEL_ID, WATERMARK_FILES))
    with pytest.raises(ValueError, match="two-dimensional"):
        _run_batch(bad_watermark, [Image.new("RGB", (10, 10), "white")] * 2)

    bad_wd14, _loader = _wd14_adapter(api, trace, logits=torch.zeros((2, 1), dtype=torch.float32))
    _load_detector(bad_wd14, _report(WD14_MODEL_ID, WD14_FILES))
    with pytest.raises(ValueError, match="tag output"):
        _run_batch(bad_wd14, [Image.new("RGB", (10, 10), "white")] * 2)

    nonfinite_watermark = _watermark_adapter(
        api,
        trace,
        logits=torch.full((1, 2), float("nan"), dtype=torch.float32),
    )
    _load_detector(nonfinite_watermark, _report(WATERMARK_MODEL_ID, WATERMARK_FILES))
    watermark_raw = _run_batch(nonfinite_watermark, [Image.new("RGB", (10, 10), "white")])
    with pytest.raises(ValidationError, match="finite"):
        validate_detector_batch_outputs(
            model_id=WATERMARK_MODEL_ID,
            batch_id="batch-001",
            sample_ids=("sample-001",),
            outputs=watermark_raw,
        )

    nonfinite_ocr = _ocr_adapter(
        api,
        trace,
        outputs=(
            {
                "regions": [
                    {
                        "box": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                        "detection_score": float("inf"),
                        "recognition_score": 0.0,
                        "text": "",
                    }
                ],
                "text_area_ratio": 0.0,
            },
        ),
    )
    _load_ocr(nonfinite_ocr)
    ocr_raw = _run_batch(nonfinite_ocr, [Image.new("RGB", (10, 10), "white")])
    with pytest.raises(ValidationError, match="finite"):
        api["validate_ocr_batch_outputs"](sample_ids=("sample-001",), outputs=ocr_raw)


def test_b35_partial_load_failures_release_resources_and_close_is_idempotent() -> None:
    api = _api()
    trace = _Trace()
    watermark = _watermark_adapter(api, trace, labels={0: "No Watermark"})
    with pytest.raises(RuntimeError, match="Watermark"):
        _load_detector(watermark, _report(WATERMARK_MODEL_ID, WATERMARK_FILES))
    watermark.close()
    watermark.close()
    assert trace.watermark_close_calls == 1

    wd14, _loader = _wd14_adapter(api, trace, fail_move=True)
    with pytest.raises(RuntimeError, match="synthetic WD14 device move failure"):
        _load_detector(wd14, _report(WD14_MODEL_ID, WD14_FILES))
    wd14.close()
    wd14.close()
    assert trace.wd14_close_calls == 1

    ocr = _ocr_adapter(api, trace, score_callable=False)
    with pytest.raises(RuntimeError, match="score"):
        _load_ocr(ocr)
    ocr.close()
    ocr.close()
    assert trace.ocr_close_calls == 1


def test_b35_fake_adapters_do_not_network_download_or_load_real_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    trace = _Trace()

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("B3.5 adapter tests must remain offline and fake-only")

    import safetensors.torch as safetensors_torch
    import timm
    import transformers

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(torch.hub, "load", forbidden)
    monkeypatch.setattr(timm, "create_model", forbidden)
    monkeypatch.setattr(safetensors_torch, "load_file", forbidden)
    monkeypatch.setattr(transformers.AutoImageProcessor, "from_pretrained", forbidden)
    monkeypatch.setattr(transformers.AutoModelForImageClassification, "from_pretrained", forbidden)

    watermark = _watermark_adapter(api, trace)
    wd14, _loader = _wd14_adapter(api, trace)
    ocr = _ocr_adapter(api, trace)
    _load_detector(watermark, _report(WATERMARK_MODEL_ID, WATERMARK_FILES))
    _load_detector(wd14, _report(WD14_MODEL_ID, WD14_FILES))
    _load_ocr(ocr)
    assert _run_batch(watermark, [Image.new("RGB", (10, 10), "white")])
    assert _run_batch(wd14, [Image.new("RGB", (10, 10), "white")])
    assert _run_batch(ocr, [Image.new("RGB", (10, 10), "white")])
