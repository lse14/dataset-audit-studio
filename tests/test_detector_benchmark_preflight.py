from __future__ import annotations

import csv
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import timm
import torch
from dataset_audit_studio.benchmarks.run_config import BenchmarkRunConfig
from dataset_audit_studio.core.model_assets import AssetFile, ModelAsset, RuntimeAssets
from PIL import Image
from safetensors import torch as safetensors_torch
from safetensors.numpy import save_file


def _api() -> dict[str, Any]:
    try:
        from dataset_audit_studio.benchmarks.detector_preflight import (
            DEFAULT_DETECTOR_ADAPTERS,
            AdapterFileSpec,
            BenchmarkAdapterContract,
            CommunityForensicsAdapter,
            DetectorPreflightError,
            UniversalFakeDetectAdapter,
            WD14TaggerAdapter,
        )
    except ImportError as error:
        pytest.fail(f"Detector benchmark preflight API is not implemented: {error}")
    return {
        "AdapterFileSpec": AdapterFileSpec,
        "BenchmarkAdapterContract": BenchmarkAdapterContract,
        "CommunityForensicsAdapter": CommunityForensicsAdapter,
        "DEFAULT_DETECTOR_ADAPTERS": DEFAULT_DETECTOR_ADAPTERS,
        "DetectorPreflightError": DetectorPreflightError,
        "UniversalFakeDetectAdapter": UniversalFakeDetectAdapter,
        "WD14TaggerAdapter": WD14TaggerAdapter,
    }


def _b21_api() -> dict[str, Any]:
    try:
        from dataset_audit_studio.benchmarks.detector_preflight import (
            COMMUNITY_FORENSICS_PREPROCESSOR,
            DetectorPreflightReport,
            PreflightFileResult,
            community_forensics_raw_sigmoid_scores,
            preprocess_community_forensics_image,
        )
    except ImportError as error:
        pytest.fail(f"Community Forensics B2.1 API is not implemented: {error}")
    return {
        "COMMUNITY_FORENSICS_PREPROCESSOR": COMMUNITY_FORENSICS_PREPROCESSOR,
        "DetectorPreflightReport": DetectorPreflightReport,
        "PreflightFileResult": PreflightFileResult,
        "community_forensics_raw_sigmoid_scores": community_forensics_raw_sigmoid_scores,
        "preprocess_community_forensics_image": preprocess_community_forensics_image,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_spec(api: dict[str, Any], path: Path, relative_path: str, file_format: str) -> Any:
    return api["AdapterFileSpec"](
        path=relative_path,
        size=path.stat().st_size,
        sha256=_sha256(path),
        file_format=file_format,
    )


def _custom_community_contract(api: dict[str, Any], files: tuple[Any, ...]) -> Any:
    return api["BenchmarkAdapterContract"](
        model_id="commfor_model_384",
        source_kind="huggingface",
        source_repository="OwensLab/commfor-model-384",
        revision="6076002bf0d9dd37537f965ee2f06f826c333b61",
        declared_license="MIT",
        loader="community_forensics_vit_small_384_v1",
        dependencies=("torch", "timm", "safetensors"),
        remote_code_allowed=False,
        files=files,
        root_relative="benchmarks/commfor_model_384/6076002bf0d9dd37537f965ee2f06f826c333b61",
        runtime_asset_model_id=None,
    )


def _write_community_asset(root: Path) -> Path:
    root.mkdir(parents=True)
    path = root / "model.safetensors"
    save_file(
        {
            "head.weight": np.zeros((2, 384), dtype=np.float32),
            "head.bias": np.zeros((2,), dtype=np.float32),
        },
        str(path),
    )
    return path


def _wd14_contract(api: dict[str, Any], files: tuple[Any, ...]) -> Any:
    return api["BenchmarkAdapterContract"](
        model_id="wd14_eva02_large_v3",
        source_kind="huggingface",
        source_repository="SmilingWolf/wd-eva02-large-tagger-v3",
        revision="b25b82a03f7282e41aa2f257a52c7583b710bd1c",
        declared_license="Apache-2.0",
        loader="wd14_eva02_large_v3",
        dependencies=("torch", "timm", "safetensors"),
        remote_code_allowed=False,
        files=files,
        root_relative="registry/wd14_eva02_large_v3/b25b82a03f7282e41aa2f257a52c7583b710bd1c",
        runtime_asset_model_id=None,
    )


def _write_wd14_assets(root: Path, tags: list[str], *, num_classes: int, head_classes: int) -> None:
    root.mkdir(parents=True)
    (root / "config.json").write_text(
        json.dumps(
            {
                "architecture": "eva02_large_patch14_448",
                "num_classes": num_classes,
                "num_features": 1024,
            }
        ),
        encoding="utf-8",
    )
    with (root / "selected_tags.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("tag_id", "name", "category", "count"))
        writer.writeheader()
        for index, tag in enumerate(tags):
            writer.writerow({"tag_id": index, "name": tag, "category": 0, "count": 1})
    save_file(
        {
            "head.weight": np.zeros((head_classes, 1024), dtype=np.float32),
            "head.bias": np.zeros((head_classes,), dtype=np.float32),
        },
        str(root / "model.safetensors"),
    )


def _wd14_file_specs(api: dict[str, Any], root: Path) -> tuple[Any, ...]:
    return tuple(
        _file_spec(
            api,
            root / name,
            name,
            "json"
            if name == "config.json"
            else "safetensors"
            if name.endswith(".safetensors")
            else "csv",
        )
        for name in ("config.json", "selected_tags.csv", "model.safetensors")
    )


def _run_config(contract: Any, artifact_hashes: tuple[str, ...]) -> BenchmarkRunConfig:
    return BenchmarkRunConfig.model_validate(
        {
            "schema_version": "detector-benchmark-run/v1",
            "manifest_sha256": "a" * 64,
            "seed": 7,
            "review_top_k": 5,
            "device": "cpu",
            "precision": "float32",
            "offline": True,
            "report_only": True,
            "models": [
                {
                    "model_id": contract.model_id,
                    "role": "candidate",
                    "source_kind": contract.source_kind,
                    "source_repository": contract.source_repository,
                    "revision": contract.revision,
                    "artifact_sha256": artifact_hashes,
                    "declared_license": contract.declared_license,
                    "remote_code_allowed": False,
                }
            ],
        }
    )


def _community_contract(api: dict[str, Any]) -> Any:
    return next(
        adapter.contract
        for adapter in api["DEFAULT_DETECTOR_ADAPTERS"]
        if adapter.contract.model_id == "commfor_model_384"
    )


def _ready_community_report(b21: dict[str, Any], root: Path) -> Any:
    return b21["DetectorPreflightReport"](
        model_id="commfor_model_384",
        status="ready",
        root=root,
        files=(
            b21["PreflightFileResult"](
                path="model.safetensors",
                status="ready",
                expected_size=87_262_324,
                actual_size=87_262_324,
                expected_sha256="b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387",
                actual_sha256="b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387",
            ),
        ),
        errors=(),
        run_config_artifacts="matched",
    )


def test_default_detector_contracts_pin_the_four_approved_models() -> None:
    api = _api()
    contracts = {
        adapter.contract.model_id: adapter.contract for adapter in api["DEFAULT_DETECTOR_ADAPTERS"]
    }

    assert set(contracts) == {
        "universal_fake_detector_head",
        "watermark_siglip2",
        "commfor_model_384",
        "wd14_eva02_large_v3",
    }
    assert contracts["commfor_model_384"].source_repository == "OwensLab/commfor-model-384"
    assert contracts["commfor_model_384"].revision == "6076002bf0d9dd37537f965ee2f06f826c333b61"
    assert contracts["commfor_model_384"].files[0].path == "model.safetensors"
    assert contracts["commfor_model_384"].files[0].size == 87_262_324
    assert (
        contracts["commfor_model_384"].files[0].sha256
        == "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387"
    )
    assert contracts["commfor_model_384"].files[0].file_format == "safetensors"
    assert (
        contracts["commfor_model_384"].root_relative
        == "benchmarks/commfor_model_384/6076002bf0d9dd37537f965ee2f06f826c333b61"
    )
    assert contracts["commfor_model_384"].runtime_asset_model_id is None
    assert (
        contracts["wd14_eva02_large_v3"].source_repository == "SmilingWolf/wd-eva02-large-tagger-v3"
    )
    for contract in contracts.values():
        assert len(contract.revision) == 40
        assert contract.remote_code_allowed is False
        assert "agpl" not in contract.declared_license.casefold()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            {"source_repository": "buildborderless/commfor-model-384"},
            "official repository",
        ),
        ({"remote_code_allowed": True}, "remote_code_allowed"),
        ({"loader": "unapproved_loader"}, "loader"),
        ({"dependencies": ("torch", "timm", "requests", "safetensors")}, "dependencies"),
    ],
)
def test_community_contract_rejects_nonofficial_or_unapproved_configuration(
    tmp_path: Path,
    mutation: dict[str, Any],
    message: str,
) -> None:
    api = _api()
    original = next(
        adapter.contract
        for adapter in api["DEFAULT_DETECTOR_ADAPTERS"]
        if adapter.contract.model_id == "commfor_model_384"
    )
    adapter = api["CommunityForensicsAdapter"](original.model_copy(update=mutation))

    report = adapter.preflight(models_root=tmp_path, runtime_assets=None, run_config=None)

    assert report.status == "invalid"
    assert message in report.errors[0]


def test_community_preflight_reports_missing_without_network_or_model_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    contract = next(
        adapter.contract
        for adapter in api["DEFAULT_DETECTOR_ADAPTERS"]
        if adapter.contract.model_id == "commfor_model_384"
    )
    adapter = api["CommunityForensicsAdapter"](contract)

    def fail_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("preflight must not access the network")

    def fail_model_creation(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("preflight must not instantiate a model")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(timm, "create_model", fail_model_creation)

    report = adapter.preflight(models_root=tmp_path, runtime_assets=None, run_config=None)

    assert report.status == "missing"
    assert report.files[0].status == "missing"


def test_missing_community_asset_retains_run_config_expected_hash(
    tmp_path: Path,
) -> None:
    api = _api()
    contract = _community_contract(api)
    expected_hash = "c" * 64

    report = api["CommunityForensicsAdapter"](contract).preflight(
        models_root=tmp_path,
        runtime_assets=None,
        run_config=_run_config(contract, (expected_hash,)),
    )

    assert report.status == "invalid"
    assert report.run_config_artifacts == "not_evaluated"
    assert (
        report.files[0].expected_sha256
        == "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387"
    )
    assert any("run-config artifact hash" in error for error in report.errors)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("path", "other.safetensors", "approved file path"),
        ("size", 87_262_325, "approved file size"),
        ("sha256", "a" * 64, "approved file SHA-256"),
        ("file_format", "json", "approved file format"),
        ("root_relative", "benchmarks/commfor_model_384/other", "root_relative"),
        ("runtime_asset_model_id", "commfor_model_384", "runtime_asset_model_id"),
    ],
)
def test_community_policy_rejects_every_pinned_file_or_root_mutation(
    tmp_path: Path,
    field: str,
    value: Any,
    message: str,
) -> None:
    api = _api()
    contract = _community_contract(api)
    if field in {"path", "size", "sha256", "file_format"}:
        changed_file = contract.files[0].model_copy(update={field: value})
        changed_contract = contract.model_copy(update={"files": (changed_file,)})
    else:
        changed_contract = contract.model_copy(update={field: value})

    report = api["CommunityForensicsAdapter"](changed_contract).preflight(
        models_root=tmp_path,
        runtime_assets=None,
        run_config=None,
    )

    assert report.status == "invalid"
    assert any(message in error for error in report.errors)


def test_community_policy_rejects_self_consistent_same_size_fake_hash(
    tmp_path: Path,
) -> None:
    api = _api()
    contract = _community_contract(api)
    fake_hash = "f" * 64
    fake_file = contract.files[0].model_copy(update={"sha256": fake_hash})
    fake_contract = contract.model_copy(update={"files": (fake_file,)})

    report = api["CommunityForensicsAdapter"](fake_contract).preflight(
        models_root=tmp_path,
        runtime_assets=None,
        run_config=_run_config(fake_contract, (fake_hash,)),
    )

    assert report.status == "invalid"
    assert any("approved file SHA-256" in error for error in report.errors)


@pytest.mark.parametrize(
    ("tensors", "valid"),
    [
        (
            {
                "vit.head.weight": np.zeros((1, 384), dtype=np.float32),
                "vit.head.bias": np.zeros((1,), dtype=np.float32),
            },
            True,
        ),
        (
            {
                "head.weight": np.zeros((1, 384), dtype=np.float32),
                "head.bias": np.zeros((1,), dtype=np.float32),
            },
            False,
        ),
        (
            {
                "vit.head.weight": np.zeros((2, 384), dtype=np.float32),
                "vit.head.bias": np.zeros((2,), dtype=np.float32),
            },
            False,
        ),
    ],
)
def test_community_schema_requires_vit_prefixed_single_logit_head(
    tmp_path: Path,
    tensors: dict[str, np.ndarray],
    valid: bool,
) -> None:
    api = _api()
    root = tmp_path / "community"
    root.mkdir()
    save_file(tensors, str(root / "model.safetensors"))

    errors = api["CommunityForensicsAdapter"](_community_contract(api))._validate_semantics(
        root,
        _community_contract(api).files,
    )

    assert (not errors) is valid


def test_community_preprocessor_records_official_source_and_test_geometry() -> None:
    b21 = _b21_api()
    processor = b21["COMMUNITY_FORENSICS_PREPROCESSOR"]

    assert processor.source_repository == "OwensLab/commfor-data-preprocessor"
    assert processor.revision == "3540a3f0d688f8bf492a8aed48613b891f88047e"
    assert processor.resize_short_edge == 440
    assert processor.crop_size == 384
    assert processor.mean == (0.485, 0.456, 0.406)
    assert processor.std == (0.229, 0.224, 0.225)


@pytest.mark.parametrize("size", [(880, 440), (440, 880)])
def test_community_preprocesses_landscape_and_portrait_to_official_shape(
    size: tuple[int, int],
) -> None:
    b21 = _b21_api()

    pixels = b21["preprocess_community_forensics_image"](Image.new("RGB", size, "white"))

    assert tuple(pixels.shape) == (3, 384, 384)


def test_community_preprocesses_constant_pixels_with_imagenet_normalization() -> None:
    b21 = _b21_api()
    image = Image.new("RGB", (440, 440), (255, 128, 0))

    pixels = b21["preprocess_community_forensics_image"](image)
    expected = torch.tensor(
        [
            (1.0 - 0.485) / 0.229,
            ((128.0 / 255.0) - 0.456) / 0.224,
            (0.0 - 0.406) / 0.225,
        ],
        dtype=torch.float32,
    ).reshape(3, 1, 1)

    assert torch.allclose(pixels, expected.expand_as(pixels), atol=1e-6)


def test_community_raw_scores_use_sigmoid_without_softmax(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    b21 = _b21_api()

    def fail_softmax(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Community Forensics must not use two-class softmax")

    monkeypatch.setattr(torch, "softmax", fail_softmax)
    scores = b21["community_forensics_raw_sigmoid_scores"](
        torch.tensor([[-1.0], [0.0], [1.0]], dtype=torch.float32)
    )

    assert [set(item) for item in scores] == [{"raw_sigmoid_score"}] * 3
    assert [item["raw_sigmoid_score"] for item in scores] == pytest.approx(
        [0.26894142, 0.5, 0.73105858]
    )
    with pytest.raises(ValueError, match="single logit"):
        b21["community_forensics_raw_sigmoid_scores"](torch.zeros((1, 2)))


@pytest.mark.parametrize("failure", ["size", "hash", "extra", "revision"])
def test_candidate_preflight_rejects_bad_asset_snapshot(
    tmp_path: Path,
    failure: str,
) -> None:
    api = _api()
    root = (
        tmp_path / "benchmarks" / "commfor_model_384" / "6076002bf0d9dd37537f965ee2f06f826c333b61"
    )
    model_path = _write_community_asset(root)
    contract = _custom_community_contract(
        api,
        (_file_spec(api, model_path, "model.safetensors", "safetensors"),),
    )

    if failure == "size":
        model_path.write_bytes(model_path.read_bytes() + b"x")
    elif failure == "hash":
        save_file(
            {
                "head.weight": np.ones((2, 384), dtype=np.float32),
                "head.bias": np.zeros((2,), dtype=np.float32),
            },
            str(model_path),
        )
    elif failure == "extra":
        (root / "unexpected.bin").write_bytes(b"extra")
    else:
        wrong_root = root.with_name("0000000000000000000000000000000000000000")
        root.rename(wrong_root)

    report = api["CommunityForensicsAdapter"](contract).preflight(
        models_root=tmp_path,
        runtime_assets=None,
        run_config=None,
    )

    assert report.status == "invalid"
    assert failure in " ".join(report.errors)


def test_baseline_preflight_rejects_custom_file_contract_even_with_matching_run_config(
    tmp_path: Path,
) -> None:
    api = _api()
    root = (
        tmp_path
        / "registry"
        / "universal_fake_detector_head"
        / "76a0e3e60a8a06458707a625d269ba815a2e5919"
    )
    root.mkdir(parents=True)
    head_path = root / "fc_weights.pth"
    head_path.write_bytes(b"local-test-head")
    head_spec = _file_spec(api, head_path, "fc_weights.pth", "pytorch_weights_only")
    contract = api["BenchmarkAdapterContract"](
        model_id="universal_fake_detector_head",
        source_kind="github",
        source_repository="WisconsinAIVision/UniversalFakeDetect",
        revision="76a0e3e60a8a06458707a625d269ba815a2e5919",
        declared_license="MIT",
        loader="universal_fake_detector_head_v1",
        dependencies=("openai_clip_vit_l14",),
        remote_code_allowed=False,
        files=(head_spec,),
        root_relative=None,
        runtime_asset_model_id="universal_fake_detector_head",
    )
    clip_root = (
        tmp_path / "registry" / "openai_clip_vit_l14" / "b8cca3fd41ae0c99ba7e8951adf17d267cdb84cd"
    )
    clip_root.mkdir(parents=True)
    clip_file = clip_root / "clip.bin"
    clip_file.write_bytes(b"clip")
    assets = RuntimeAssets(
        models_root=str(tmp_path),
        models=(
            ModelAsset(
                model_id="openai_clip_vit_l14",
                loader="openai_clip_vit_l14_v1",
                root=str(clip_root),
                files=(
                    AssetFile(
                        path="clip.bin",
                        size=clip_file.stat().st_size,
                        sha256=_sha256(clip_file),
                        mtime_ns=clip_file.stat().st_mtime_ns,
                    ),
                ),
                dependencies=(),
                is_custom=False,
                base_model_id=None,
            ),
            ModelAsset(
                model_id=contract.model_id,
                loader=contract.loader,
                root=str(root),
                files=(
                    AssetFile(
                        path=head_spec.path,
                        size=head_spec.size,
                        sha256=head_spec.sha256,
                        mtime_ns=head_path.stat().st_mtime_ns,
                    ),
                ),
                dependencies=contract.dependencies,
                is_custom=False,
                base_model_id=None,
            ),
        ),
    )
    adapter = api["UniversalFakeDetectAdapter"](contract)

    ready = adapter.preflight(
        models_root=tmp_path,
        runtime_assets=assets,
        run_config=_run_config(contract, (head_spec.sha256,)),
    )
    mismatch = adapter.preflight(
        models_root=tmp_path,
        runtime_assets=assets,
        run_config=_run_config(contract, ("f" * 64,)),
    )

    assert ready.status == "invalid"
    assert any("approved file" in error for error in ready.errors)
    assert mismatch.status == "invalid"
    assert any("approved file" in error for error in mismatch.errors)


def test_baseline_preflight_requires_declared_runtime_asset_dependency(
    tmp_path: Path,
) -> None:
    api = _api()
    root = (
        tmp_path
        / "registry"
        / "universal_fake_detector_head"
        / "76a0e3e60a8a06458707a625d269ba815a2e5919"
    )
    root.mkdir(parents=True)
    head_path = root / "fc_weights.pth"
    head_path.write_bytes(b"local-test-head")
    head_spec = _file_spec(api, head_path, "fc_weights.pth", "pytorch_weights_only")
    contract = api["BenchmarkAdapterContract"](
        model_id="universal_fake_detector_head",
        source_kind="github",
        source_repository="WisconsinAIVision/UniversalFakeDetect",
        revision="76a0e3e60a8a06458707a625d269ba815a2e5919",
        declared_license="MIT",
        loader="universal_fake_detector_head_v1",
        dependencies=("openai_clip_vit_l14",),
        remote_code_allowed=False,
        files=(head_spec,),
        root_relative=None,
        runtime_asset_model_id="universal_fake_detector_head",
    )
    assets = RuntimeAssets(
        models_root=str(tmp_path),
        models=(
            ModelAsset(
                model_id=contract.model_id,
                loader=contract.loader,
                root=str(root),
                files=(
                    AssetFile(
                        path=head_spec.path,
                        size=head_spec.size,
                        sha256=head_spec.sha256,
                        mtime_ns=head_path.stat().st_mtime_ns,
                    ),
                ),
                dependencies=contract.dependencies,
                is_custom=False,
                base_model_id=None,
            ),
        ),
    )

    report = api["UniversalFakeDetectAdapter"](contract).preflight(
        models_root=tmp_path,
        runtime_assets=assets,
        run_config=None,
    )

    assert report.status == "invalid"
    assert "RuntimeAssets snapshot is missing dependency: openai_clip_vit_l14" in report.errors


@pytest.mark.parametrize("failure", ["missing_tag", "duplicate_tag", "output_dimension"])
def test_wd14_preflight_validates_tag_table_and_safetensors_output(
    tmp_path: Path,
    failure: str,
) -> None:
    api = _api()
    required_tags = [
        "watermark",
        "signature",
        "logo",
        "artist_logo",
        "sample_watermark",
        "english_text",
        "chinese_text",
        "korean_text",
        "text_focus",
        "romaji_text",
        "mixed-language_text",
    ]
    root = (
        tmp_path / "registry" / "wd14_eva02_large_v3" / "b25b82a03f7282e41aa2f257a52c7583b710bd1c"
    )
    tags = required_tags.copy()
    num_classes = len(tags)
    head_classes = len(tags)
    if failure == "missing_tag":
        tags.remove("sample_watermark")
        num_classes = len(tags)
        head_classes = len(tags)
    elif failure == "duplicate_tag":
        tags.append("logo")
        num_classes = len(tags)
        head_classes = len(tags)
    else:
        num_classes += 1
    _write_wd14_assets(root, tags, num_classes=num_classes, head_classes=head_classes)
    contract = _wd14_contract(api, _wd14_file_specs(api, root))

    errors = api["WD14TaggerAdapter"](contract)._validate_semantics(root, contract.files)

    assert failure.replace("_", " ") in " ".join(errors)


def test_preflight_is_read_only_and_load_blocks_nonready_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    source_image = tmp_path / "source.png"
    database = tmp_path / "app.db"
    registry = tmp_path / "registry.json"
    source_image.write_bytes(b"source")
    database.write_bytes(b"database")
    registry.write_bytes(b"registry")
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (source_image, database, registry)
    }
    contract = next(
        adapter.contract
        for adapter in api["DEFAULT_DETECTOR_ADAPTERS"]
        if adapter.contract.model_id == "commfor_model_384"
    )
    adapter = api["CommunityForensicsAdapter"](contract)
    report = adapter.preflight(models_root=tmp_path, runtime_assets=None, run_config=None)

    def fail_model_creation(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("non-ready assets must not instantiate a model")

    monkeypatch.setattr(timm, "create_model", fail_model_creation)
    with pytest.raises(api["DetectorPreflightError"], match="not ready"):
        adapter.load(report)

    assert report.status == "missing"
    for path, expected in before.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == expected


def test_community_load_rebuilds_local_vit_without_pretrained_weights(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    b21 = _b21_api()
    root = tmp_path / "community-ready"
    root.mkdir()
    report = _ready_community_report(b21, root)
    calls: list[tuple[str, bool]] = []
    strict_values: list[bool] = []

    class DummyModel(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.head = torch.nn.Identity()

        def forward(self, pixels: torch.Tensor) -> torch.Tensor:
            return self.head(pixels)

    def create_model(name: str, *, pretrained: bool) -> DummyModel:
        calls.append((name, pretrained))
        return DummyModel()

    state = {
        "vit.head.weight": torch.zeros((1, 384), dtype=torch.float32),
        "vit.head.bias": torch.zeros((1,), dtype=torch.float32),
    }
    original_load_state_dict = torch.nn.Module.load_state_dict

    def track_load_state_dict(
        model: torch.nn.Module,
        state_dict: dict[str, torch.Tensor],
        strict: bool = True,
        assign: bool = False,
    ) -> Any:
        strict_values.append(strict)
        return original_load_state_dict(model, state_dict, strict=strict, assign=assign)

    monkeypatch.setattr(timm, "create_model", create_model)
    monkeypatch.setattr(safetensors_torch, "load_file", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(torch.nn.Module, "load_state_dict", track_load_state_dict)

    loaded = api["CommunityForensicsAdapter"](_community_contract(api)).load(report)

    assert loaded.model is not None
    assert calls == [("vit_small_patch16_384.augreg_in21k_ft_in1k", False)]
    assert isinstance(loaded.model.vit.head, torch.nn.Linear)
    assert loaded.model.vit.head.in_features == 384
    assert loaded.model.vit.head.out_features == 1
    assert strict_values == [True]
