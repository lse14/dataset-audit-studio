from __future__ import annotations

import hashlib
import json
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import torch
from dataset_audit_studio.benchmarks.detector_preflight import (
    COMMUNITY_FORENSICS_CONTRACT,
    DetectorPreflightReport,
    PreflightFileResult,
)
from dataset_audit_studio.benchmarks.manifest import BenchmarkManifest, BenchmarkManifestEntry
from dataset_audit_studio.benchmarks.run_config import BenchmarkRunConfigV2
from dataset_audit_studio.benchmarks.sidecar import validate_detector_batch_outputs
from dataset_audit_studio.core.model_assets import AssetFile, ModelAsset, RuntimeAssets
from PIL import Image

UFD_MODEL_ID = "universal_fake_detector_head"
CLIP_MODEL_ID = "openai_clip_vit_l14"
COMMUNITY_MODEL_ID = "commfor_model_384"
HEAD_SHA256 = "a" * 64
CLIP_SHA256 = "b" * 64
COMMUNITY_SHA256 = "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387"


def _api() -> dict[str, Any]:
    try:
        from dataset_audit_studio.benchmarks.detector_adapters import (
            CommunityForensicsBenchmarkAdapter,
            UniversalFakeDetectBenchmarkAdapter,
        )
        from dataset_audit_studio.benchmarks.harness import (
            BenchmarkExecutionEnvironment,
            run_detector_benchmark,
        )
    except ImportError as error:
        pytest.fail(f"B3.4 benchmark adapter API is not implemented: {error}")
    return {
        "CommunityForensicsBenchmarkAdapter": CommunityForensicsBenchmarkAdapter,
        "UniversalFakeDetectBenchmarkAdapter": UniversalFakeDetectBenchmarkAdapter,
        "BenchmarkExecutionEnvironment": BenchmarkExecutionEnvironment,
        "run_detector_benchmark": run_detector_benchmark,
    }


@dataclass
class _Trace:
    events: list[str] = field(default_factory=list)
    clip_batch_sizes: list[int] = field(default_factory=list)
    head_batch_sizes: list[int] = field(default_factory=list)
    community_move_devices: list[str] = field(default_factory=list)
    clip_close_calls: int = 0
    head_close_calls: int = 0
    community_close_calls: int = 0


class _FakeClipModel:
    def __init__(self, trace: _Trace) -> None:
        self._trace = trace

    def encode_image(self, pixels: torch.Tensor) -> torch.Tensor:
        self._trace.events.append("clip")
        self._trace.clip_batch_sizes.append(int(pixels.shape[0]))
        return torch.full((pixels.shape[0], 768), 0.5, dtype=torch.float32)


class _FakeHead:
    def __init__(self, trace: _Trace, *, logits: torch.Tensor | None = None) -> None:
        self._trace = trace
        self._logits = logits

    def __call__(self, features: torch.Tensor) -> torch.Tensor:
        self._trace.events.append("head")
        self._trace.head_batch_sizes.append(int(features.shape[0]))
        if self._logits is not None:
            return self._logits
        return torch.zeros((features.shape[0], 1), dtype=torch.float32)


class _FakeClipRuntime:
    def __init__(self, trace: _Trace) -> None:
        self._trace = trace
        self.model = _FakeClipModel(trace)

    def ufd_preprocess(self, image: Image.Image) -> torch.Tensor:
        self._trace.events.append("clip-preprocess")
        assert image.mode == "RGB"
        return torch.zeros((3, 2, 2), dtype=torch.float32)

    def close(self) -> None:
        self._trace.clip_close_calls += 1
        self.model = None


class _FakeHeadRuntime:
    def __init__(self, trace: _Trace, *, logits: torch.Tensor | None = None) -> None:
        self.head = _FakeHead(trace, logits=logits)
        self._trace = trace

    def close(self) -> None:
        self._trace.head_close_calls += 1
        self.head = None


class _FakeCommunityModel:
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

    def to(self, _device: str) -> _FakeCommunityModel:
        self._trace.events.append("community-to")
        self._trace.community_move_devices.append(str(_device))
        if self._fail_move:
            raise RuntimeError("synthetic Community device move failure")
        return self

    def __call__(self, pixels: torch.Tensor) -> torch.Tensor:
        self._trace.events.append("community-vit")
        if self._logits is not None:
            return self._logits
        return torch.zeros((pixels.shape[0], 1), dtype=torch.float32)

    def close(self) -> None:
        self._trace.community_close_calls += 1


class _FakeCommunityLoader:
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
        self._trace.events.append("community-loader")
        return type("Loaded", (), {
            "model": _FakeCommunityModel(
                self._trace,
                logits=self._logits,
                fail_move=self._fail_move,
            )
        })()


def _asset(
    model_id: str,
    path: str,
    sha256: str,
    size: int,
) -> ModelAsset:
    return ModelAsset(
        model_id=model_id,
        loader=f"{model_id}_loader",
        root=".",
        files=(AssetFile(path=path, size=size, sha256=sha256, mtime_ns=1),),
        dependencies=(),
        is_custom=False,
        base_model_id=None,
    )


def _ufd_assets(*, include_clip: bool = True) -> RuntimeAssets:
    models = [_asset(UFD_MODEL_ID, "fc_weights.pth", HEAD_SHA256, 4_083)]
    if include_clip:
        models.append(_asset(CLIP_MODEL_ID, "ViT-L-14.pt", CLIP_SHA256, 932_768_134))
    return RuntimeAssets(models_root=".", models=tuple(models))


def _report(
    model_id: str,
    *,
    status: str = "ready",
    artifact_status: str = "matched",
) -> DetectorPreflightReport:
    if model_id == UFD_MODEL_ID:
        file = PreflightFileResult(
            path="fc_weights.pth",
            status="ready",
            expected_size=4_083,
            actual_size=4_083,
            expected_sha256=HEAD_SHA256,
            actual_sha256=HEAD_SHA256,
        )
    else:
        file = PreflightFileResult(
            path="model.safetensors",
            status="ready",
            expected_size=87_262_324,
            actual_size=87_262_324,
            expected_sha256=COMMUNITY_SHA256,
            actual_sha256=COMMUNITY_SHA256,
        )
    return DetectorPreflightReport(
        model_id=model_id,
        status=status,  # type: ignore[arg-type]
        root=Path("."),
        files=(file,),
        errors=(),
        run_config_artifacts=artifact_status,  # type: ignore[arg-type]
    )


def _ufd_adapter(
    api: dict[str, Any],
    trace: _Trace,
    *,
    include_clip: bool = True,
    head_logits: torch.Tensor | None = None,
    fail_head_factory: bool = False,
) -> Any:
    def build_clip(*_args: Any, **_kwargs: Any) -> _FakeClipRuntime:
        trace.events.append("clip-load")
        return _FakeClipRuntime(trace)

    def build_head(*_args: Any, **_kwargs: Any) -> _FakeHeadRuntime:
        trace.events.append("head-load")
        if fail_head_factory:
            raise RuntimeError("synthetic head load failure")
        return _FakeHeadRuntime(trace, logits=head_logits)

    return api["UniversalFakeDetectBenchmarkAdapter"](
        runtime_assets=_ufd_assets(include_clip=include_clip),
        clip_runtime_factory=build_clip,
        head_runtime_factory=build_head,
    )


def _community_adapter(
    api: dict[str, Any],
    trace: _Trace,
    *,
    logits: torch.Tensor | None = None,
    preprocess_image: Callable[[Image.Image], torch.Tensor] | None = None,
    fail_move: bool = False,
) -> tuple[Any, _FakeCommunityLoader]:
    loader = _FakeCommunityLoader(trace, logits=logits, fail_move=fail_move)
    adapter = api["CommunityForensicsBenchmarkAdapter"](
        community_loader=loader,
        preprocess_image=preprocess_image,
    )
    return adapter, loader


def _load(adapter: Any, report: DetectorPreflightReport) -> None:
    adapter.load(
        preflight_report=report,
        requested_device="cpu",
        requested_precision="float32",
    )


def _run_batch(adapter: Any, images: Sequence[Image.Image]) -> list[dict[str, float]]:
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
                    "strata": ["human_anime"],
                    "ai_origin": _annotation("human"),
                    "watermark_labels": {
                        "watermark": _annotation("absent"),
                        "signature": _annotation("absent"),
                        "logo": _annotation("absent"),
                        "artist_logo": _annotation("absent"),
                        "sample_watermark": _annotation("absent"),
                        "text": _annotation("absent"),
                    },
                }
            )
        )
    return images_root, BenchmarkManifest(entries=tuple(reversed(entries)))


def _config(
    manifest: BenchmarkManifest,
    model_id: str,
    artifact_sha256: str,
) -> BenchmarkRunConfigV2:
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
            "models": [
                {
                    "model_id": model_id,
                    "role": "baseline",
                    "source_kind": "github",
                    "source_repository": "Example/Offline",
                    "revision": "a" * 40,
                    "artifact_sha256": [artifact_sha256],
                    "declared_license": "MIT",
                    "remote_code_allowed": False,
                    "batch_size": 2,
                }
            ],
        }
    )


def test_ufd_harness_recomputes_clip_then_head_for_every_batch_and_records_dependency(
    tmp_path: Path,
) -> None:
    api = _api()
    trace = _Trace()
    adapter = _ufd_adapter(api, trace)
    images_root, manifest = _manifest(tmp_path)
    config = _config(manifest, UFD_MODEL_ID, HEAD_SHA256)
    environment = api["BenchmarkExecutionEnvironment"](
        actual_device="cpu",
        actual_precision="float32",
        software={"test": "b34"},
        hardware={"test": "fake"},
        memory_measurement_supported=False,
    )

    result = api["run_detector_benchmark"](
        manifest=manifest,
        run_config=config,
        preflight_reports=(_report(UFD_MODEL_ID),),
        images_root=images_root,
        output_directory=tmp_path / "sidecar",
        adapter_factories={UFD_MODEL_ID: lambda: adapter},
        execution_environment=environment,
    )

    assert trace.clip_batch_sizes == [2, 2, 1, 2, 1]
    assert trace.head_batch_sizes == [2, 2, 1, 2, 1]
    assert [event for event in trace.events if event in {"clip", "head"}] == [
        "clip",
        "head",
    ] * 5
    provenance = result.run.detector_provenance[0]
    assert provenance.model_id == UFD_MODEL_ID
    assert [(asset.asset_id, asset.role) for asset in provenance.assets] == [
        (UFD_MODEL_ID, "model"),
        (CLIP_MODEL_ID, "dependency"),
    ]
    assert provenance.assets[0].files[0].sha256 == HEAD_SHA256
    assert provenance.assets[0].files[0].size_bytes == 4_083
    assert provenance.assets[1].files[0].sha256 == CLIP_SHA256
    assert provenance.assets[1].files[0].size_bytes == 932_768_134
    assert trace.clip_close_calls == 1
    assert trace.head_close_calls == 1
    scores_path = result.output_directory / "scores.jsonl"
    scores = [json.loads(line) for line in scores_path.read_text(encoding="utf-8").splitlines()]
    assert len(scores) == 3
    assert all(set(score) >= {"raw_sigmoid_score"} for score in scores)
    assert all("probability" not in score for score in scores)


@pytest.mark.parametrize("status", ["missing", "invalid"])
def test_adapters_reject_nonready_or_unmatched_preflight_before_loading(
    status: str,
) -> None:
    api = _api()
    trace = _Trace()
    ufd = _ufd_adapter(api, trace)
    community, loader = _community_adapter(api, trace)

    with pytest.raises(RuntimeError, match="ready.*matched"):
        _load(ufd, _report(UFD_MODEL_ID, status=status))
    with pytest.raises(RuntimeError, match="ready.*matched"):
        _load(community, _report(COMMUNITY_MODEL_ID, artifact_status="mismatch"))

    assert "clip-load" not in trace.events
    assert "community-loader" not in trace.events
    assert loader.report is None


def test_ufd_rejects_missing_clip_dependency_before_constructing_runtime() -> None:
    api = _api()
    trace = _Trace()
    adapter = _ufd_adapter(api, trace, include_clip=False)

    with pytest.raises(RuntimeError, match=CLIP_MODEL_ID):
        _load(adapter, _report(UFD_MODEL_ID))

    assert trace.events == []


def test_ufd_closes_partially_loaded_clip_and_remains_idempotent() -> None:
    api = _api()
    trace = _Trace()
    adapter = _ufd_adapter(api, trace, fail_head_factory=True)

    with pytest.raises(RuntimeError, match="synthetic head load failure"):
        _load(adapter, _report(UFD_MODEL_ID))
    adapter.close()
    adapter.close()

    assert trace.clip_close_calls == 1
    assert trace.head_close_calls == 0


def test_community_reuses_b21_local_transform_loader_and_raw_sigmoid_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    trace = _Trace()
    adapter, loader = _community_adapter(api, trace)

    def fail_softmax(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Community benchmark adapter must not use softmax")

    monkeypatch.setattr(torch, "softmax", fail_softmax)
    _load(adapter, _report(COMMUNITY_MODEL_ID))
    outputs = _run_batch(
        adapter,
        [Image.new("RGB", (440, 440), "white"), Image.new("RGB", (440, 880), "black")],
    )

    assert loader.report is not None
    assert loader.report.model_id == COMMUNITY_MODEL_ID
    assert outputs == [
        {"raw_sigmoid_score": pytest.approx(0.5)},
        {"raw_sigmoid_score": pytest.approx(0.5)},
    ]
    assert adapter.provenance.preprocessing.source == "OwensLab/commfor-data-preprocessor"
    assert adapter.provenance.preprocessing.revision == "3540a3f0d688f8bf492a8aed48613b891f88047e"
    assert adapter.provenance.assets[0].files[0].sha256 == COMMUNITY_SHA256
    assert adapter.provenance.assets[0].files[0].size_bytes == 87_262_324

    adapter.close()
    adapter.close()
    assert trace.community_close_calls == 1


def test_community_closes_model_when_load_fails_after_model_creation() -> None:
    api = _api()
    trace = _Trace()
    adapter, _loader = _community_adapter(api, trace, fail_move=True)

    with pytest.raises(RuntimeError, match="synthetic Community device move failure"):
        _load(adapter, _report(COMMUNITY_MODEL_ID))
    adapter.close()
    adapter.close()

    assert trace.community_close_calls == 1


def test_community_load_resolves_auto_device_before_moving_the_b21_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    trace = _Trace()
    adapter, _loader = _community_adapter(api, trace)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    adapter.load(
        preflight_report=_report(COMMUNITY_MODEL_ID),
        requested_device="auto",
        requested_precision="float32",
    )

    assert trace.community_move_devices == ["cpu"]


def test_community_default_adapter_wires_the_b21_loader_and_preprocessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    import dataset_audit_studio.benchmarks.detector_adapters as adapters

    trace = _Trace()

    class B21Loader:
        def __init__(self, contract: Any) -> None:
            assert contract is COMMUNITY_FORENSICS_CONTRACT

        def load(self, report: DetectorPreflightReport) -> Any:
            assert report.model_id == COMMUNITY_MODEL_ID
            trace.events.append("b21-loader")
            return type("Loaded", (), {"model": _FakeCommunityModel(trace)})()

    def b21_preprocess(image: Image.Image) -> torch.Tensor:
        trace.events.append("b21-preprocess")
        assert image.mode == "RGB"
        return torch.zeros((3, 4, 4), dtype=torch.float32)

    monkeypatch.setattr(adapters, "CommunityForensicsPreflightAdapter", B21Loader)
    monkeypatch.setattr(adapters, "preprocess_community_forensics_image", b21_preprocess)
    adapter = api["CommunityForensicsBenchmarkAdapter"]()

    _load(adapter, _report(COMMUNITY_MODEL_ID))
    outputs = _run_batch(adapter, [Image.new("RGB", (440, 440), "white")])

    assert trace.events[:3] == ["b21-loader", "community-to", "b21-preprocess"]
    assert outputs == [{"raw_sigmoid_score": pytest.approx(0.5)}]


@pytest.mark.parametrize(
    ("model_id", "logits", "message"),
    [
        (UFD_MODEL_ID, torch.zeros((2, 2)), "single logit"),
        (COMMUNITY_MODEL_ID, torch.zeros((2, 2)), "single logit"),
    ],
)
def test_adapters_reject_non_single_logit_batch_shapes(
    model_id: str,
    logits: torch.Tensor,
    message: str,
) -> None:
    api = _api()
    trace = _Trace()
    if model_id == UFD_MODEL_ID:
        adapter = _ufd_adapter(api, trace, head_logits=logits)
    else:
        adapter, _loader = _community_adapter(api, trace, logits=logits)
    _load(adapter, _report(model_id))

    with pytest.raises(ValueError, match=message):
        _run_batch(adapter, [Image.new("RGB", (440, 440), "white")] * 2)


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf")])
def test_adapters_leave_nonfinite_raw_scores_for_sidecar_rejection(
    nonfinite: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    trace = _Trace()

    def nonfinite_sigmoid(values: torch.Tensor) -> torch.Tensor:
        return torch.full_like(values, nonfinite)

    monkeypatch.setattr(torch, "sigmoid", nonfinite_sigmoid)
    ufd = _ufd_adapter(api, trace)
    community, _loader = _community_adapter(api, trace)

    for model_id, adapter in ((UFD_MODEL_ID, ufd), (COMMUNITY_MODEL_ID, community)):
        _load(adapter, _report(model_id))
        raw = _run_batch(adapter, [Image.new("RGB", (440, 440), "white")])
        assert set(raw[0]) == {"raw_sigmoid_score"}
        with pytest.raises(ValueError, match="finite"):
            validate_detector_batch_outputs(
                model_id=model_id,
                batch_id="batch-001",
                sample_ids=("sample-001",),
                outputs=raw,
            )


def test_injected_adapters_do_not_network_download_remote_code_or_load_real_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    trace = _Trace()

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("B3.4 adapter test must remain offline and local")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(torch.hub, "load", forbidden)
    ufd = _ufd_adapter(api, trace)
    community, loader = _community_adapter(api, trace)

    _load(ufd, _report(UFD_MODEL_ID))
    _load(community, _report(COMMUNITY_MODEL_ID))
    assert _run_batch(ufd, [Image.new("RGB", (440, 440), "white")])
    assert _run_batch(community, [Image.new("RGB", (440, 440), "white")])
    assert loader.report is not None


def test_community_provenance_uses_the_pinned_b21_model_contract() -> None:
    api = _api()
    trace = _Trace()
    adapter, _loader = _community_adapter(api, trace)
    _load(adapter, _report(COMMUNITY_MODEL_ID))

    asset = adapter.provenance.assets[0]
    assert asset.asset_id == COMMUNITY_FORENSICS_CONTRACT.model_id
    assert asset.role == "model"
    assert asset.files[0].path == COMMUNITY_FORENSICS_CONTRACT.files[0].path
    assert asset.files[0].sha256 == COMMUNITY_FORENSICS_CONTRACT.files[0].sha256
    assert asset.files[0].size_bytes == COMMUNITY_FORENSICS_CONTRACT.files[0].size
