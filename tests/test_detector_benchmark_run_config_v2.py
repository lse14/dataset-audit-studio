from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError


def _api() -> dict[str, Any]:
    try:
        from dataset_audit_studio.benchmarks.run_config import (
            RUN_CONFIG_SCHEMA_VERSION,
            RUN_CONFIG_V2_SCHEMA_VERSION,
            BenchmarkRunConfig,
            BenchmarkRunConfigV2,
            canonical_run_config_sha256,
            validate_run_config_manifest,
        )
    except ImportError as error:
        pytest.fail(f"Benchmark run-config v2 API is not implemented: {error}")
    return {
        "RUN_CONFIG_SCHEMA_VERSION": RUN_CONFIG_SCHEMA_VERSION,
        "RUN_CONFIG_V2_SCHEMA_VERSION": RUN_CONFIG_V2_SCHEMA_VERSION,
        "BenchmarkRunConfig": BenchmarkRunConfig,
        "BenchmarkRunConfigV2": BenchmarkRunConfigV2,
        "canonical_run_config_sha256": canonical_run_config_sha256,
        "validate_run_config_manifest": validate_run_config_manifest,
    }


def _detector_reference(
    *,
    model_id: str = "universal_fake_detector_head",
    role: str = "baseline",
    batch_size: int = 8,
    artifact_sha256: str = "B" * 64,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "role": role,
        "source_kind": "github",
        "source_repository": "WisconsinAIVision/UniversalFakeDetect",
        "revision": "76A0E3E60A8A06458707A625D269BA815A2E5919",
        "artifact_sha256": [artifact_sha256],
        "declared_license": "MIT",
        "remote_code_allowed": False,
        "batch_size": batch_size,
    }


def _ocr_model_reference(
    *,
    model_id: str,
    repository: str,
    revision: str,
    batch_size: int,
    artifact_sha256: str,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "source_kind": "huggingface",
        "source_repository": repository,
        "revision": revision,
        "artifact_sha256": [artifact_sha256],
        "declared_license": "apache-2.0",
        "remote_code_allowed": False,
        "batch_size": batch_size,
    }


def _auxiliary_ocr() -> dict[str, Any]:
    return {
        "detector": _ocr_model_reference(
            model_id="ppocrv5_server_det",
            repository="PaddlePaddle/PP-OCRv5_server_det_safetensors",
            revision="CBEA9F3C3254C6FF7B0016CFBF90549E1AD4C5BB",
            batch_size=4,
            artifact_sha256="C" * 64,
        ),
        "recognizer": _ocr_model_reference(
            model_id="ppocrv5_server_rec",
            repository="PaddlePaddle/PP-OCRv5_server_rec_safetensors",
            revision="542979D7CC3791732BB12AF35313A6840952D79F",
            batch_size=16,
            artifact_sha256="D" * 64,
        ),
    }


def _v2_payload(*, include_ocr: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "detector-benchmark-run/v2",
        "manifest_sha256": "A" * 64,
        "seed": 17,
        "review_top_k": 7,
        "device": "cpu",
        "precision": "float32",
        "offline": True,
        "report_only": True,
        "warmup_batches": 2,
        "timing_repeats": 3,
        "models": [_detector_reference()],
    }
    if include_ocr:
        payload["auxiliary_ocr"] = _auxiliary_ocr()
    return payload


def _v1_payload() -> dict[str, Any]:
    model = _detector_reference()
    model.pop("batch_size")
    payload = _v2_payload(include_ocr=False)
    payload["schema_version"] = "detector-benchmark-run/v1"
    payload["models"] = [model]
    payload.pop("warmup_batches")
    payload.pop("timing_repeats")
    return payload


def test_v1_api_remains_compatible_while_v2_requires_execution_fields() -> None:
    api = _api()

    v1 = api["BenchmarkRunConfig"].model_validate(_v1_payload())
    v2 = api["BenchmarkRunConfigV2"].model_validate(_v2_payload())

    assert api["RUN_CONFIG_SCHEMA_VERSION"] == "detector-benchmark-run/v1"
    assert api["RUN_CONFIG_V2_SCHEMA_VERSION"] == "detector-benchmark-run/v2"
    assert v1.schema_version == "detector-benchmark-run/v1"
    assert v2.schema_version == "detector-benchmark-run/v2"
    assert v2.models[0].batch_size == 8
    assert v2.warmup_batches == 2
    assert v2.timing_repeats == 3
    assert v2.auxiliary_ocr is not None
    assert v2.auxiliary_ocr.detector.batch_size == 4
    assert v2.auxiliary_ocr.recognizer.batch_size == 16


def test_v2_auxiliary_ocr_is_optional() -> None:
    api = _api()

    config = api["BenchmarkRunConfigV2"].model_validate(_v2_payload(include_ocr=False))

    assert config.auxiliary_ocr is None


def test_v2_hash_is_canonical_and_preserves_model_execution_order() -> None:
    api = _api()
    payload = _v2_payload()
    payload["models"].append(
        {
            **_detector_reference(
                model_id="commfor_model_384",
                role="candidate",
                batch_size=2,
                artifact_sha256="E" * 64,
            ),
            "source_kind": "huggingface",
            "source_repository": "OwensLab/commfor-model-384",
            "revision": "6076002BF0D9DD37537F965EE2F06F826C333B61",
        }
    )
    config = api["BenchmarkRunConfigV2"].model_validate(payload)
    reordered_keys = api["BenchmarkRunConfigV2"].model_validate(
        dict(reversed(list(payload.items())))
    )
    reversed_models_payload = copy.deepcopy(payload)
    reversed_models_payload["models"].reverse()
    reversed_models = api["BenchmarkRunConfigV2"].model_validate(reversed_models_payload)
    canonical_bytes = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = hashlib.sha256(canonical_bytes).hexdigest()

    assert config.canonical_sha256 == expected
    assert api["canonical_run_config_sha256"](config) == expected
    assert reordered_keys.canonical_sha256 == expected
    assert reversed_models.canonical_sha256 != expected
    assert config.manifest_sha256 == "a" * 64
    assert config.models[0].revision == "76a0e3e60a8a06458707a625d269ba815a2e5919"
    assert config.models[0].artifact_sha256 == ("b" * 64,)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("warmup_batches",), -1),
        (("timing_repeats",), 0),
        (("models", 0, "batch_size"), 0),
        (("auxiliary_ocr", "detector", "batch_size"), 0),
        (("auxiliary_ocr", "recognizer", "batch_size"), 0),
    ],
)
def test_v2_rejects_invalid_timing_and_batch_sizes(
    path: tuple[str | int, ...],
    value: int,
) -> None:
    api = _api()
    payload = _v2_payload()
    target: Any = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        api["BenchmarkRunConfigV2"].model_validate(payload)


@pytest.mark.parametrize(
    "location",
    ["config", "model", "auxiliary_ocr", "ocr_detector"],
)
def test_v2_rejects_thresholds_and_unknown_fields(location: str) -> None:
    api = _api()
    payload = _v2_payload()
    if location == "config":
        payload["score_threshold"] = 0.5
    elif location == "model":
        payload["models"][0]["score_threshold"] = 0.5
    elif location == "auxiliary_ocr":
        payload["auxiliary_ocr"]["score_threshold"] = 0.5
    else:
        payload["auxiliary_ocr"]["detector"]["score_threshold"] = 0.5

    with pytest.raises(ValidationError, match="Extra inputs"):
        api["BenchmarkRunConfigV2"].model_validate(payload)


def test_v2_rejects_duplicate_detector_model_ids() -> None:
    api = _api()
    payload = _v2_payload()
    payload["models"].append(copy.deepcopy(payload["models"][0]))

    with pytest.raises(ValidationError, match="model_id values must be unique"):
        api["BenchmarkRunConfigV2"].model_validate(payload)


@pytest.mark.parametrize(
    ("slot", "model_id", "message"),
    [
        ("detector", "ppocrv5_server_rec", "detector must be ppocrv5_server_det"),
        ("recognizer", "ppocrv5_server_det", "recognizer must be ppocrv5_server_rec"),
    ],
)
def test_v2_auxiliary_ocr_requires_fixed_detector_and_recognizer_roles(
    slot: str,
    model_id: str,
    message: str,
) -> None:
    api = _api()
    payload = _v2_payload()
    payload["auxiliary_ocr"][slot]["model_id"] = model_id

    with pytest.raises(ValidationError, match=message):
        api["BenchmarkRunConfigV2"].model_validate(payload)


def test_v2_rejects_ocr_models_in_detector_list() -> None:
    api = _api()
    payload = _v2_payload(include_ocr=False)
    payload["models"][0]["model_id"] = "ppocrv5_server_det"

    with pytest.raises(
        ValidationError,
        match="OCR model identities must be configured under auxiliary_ocr",
    ):
        api["BenchmarkRunConfigV2"].model_validate(payload)


def test_v2_manifest_binding_uses_the_normalized_manifest_hash() -> None:
    api = _api()
    config = api["BenchmarkRunConfigV2"].model_validate(_v2_payload())
    manifest = SimpleNamespace(canonical_sha256="a" * 64)

    api["validate_run_config_manifest"](config, manifest)
    mismatched = config.model_copy(update={"manifest_sha256": "f" * 64})
    with pytest.raises(ValueError, match="does not match manifest"):
        api["validate_run_config_manifest"](mismatched, manifest)
