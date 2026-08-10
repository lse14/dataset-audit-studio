from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError


def _manifest_api() -> tuple[Any, Any, Any]:
    try:
        from dataset_audit_studio.benchmarks.manifest import (
            BenchmarkManifest,
            BenchmarkManifestEntry,
            load_manifest,
        )
    except ImportError as error:
        pytest.fail(f"Benchmark manifest API is not implemented: {error}")
    return BenchmarkManifest, BenchmarkManifestEntry, load_manifest


def _run_config_api() -> tuple[Any, Any]:
    try:
        from dataset_audit_studio.benchmarks.run_config import (
            BenchmarkRunConfig,
            validate_run_config_manifest,
        )
    except ImportError as error:
        pytest.fail(f"Benchmark run-config API is not implemented: {error}")
    return BenchmarkRunConfig, validate_run_config_manifest


def _annotation(value: str, *, trust: str = "trusted") -> dict[str, str]:
    return {
        "value": value,
        "label_source": "manual:benchmark-review-v1",
        "label_trust": trust,
    }


def _entry_payload(
    *,
    sample_id: str = "danbooru-human-0001",
    source_corpus: str = "danbooru",
    image_sha256: str = "a" * 64,
) -> dict[str, Any]:
    return {
        "schema_version": "detector-benchmark-manifest/v1",
        "sample_id": sample_id,
        "image_path": f"{source_corpus}/{sample_id}.png",
        "image_sha256": image_sha256,
        "source_corpus": source_corpus,
        "strata": ["human_anime", "ordinary_text"],
        "derivation": None,
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


def test_manifest_hash_is_canonical_and_independent_of_jsonl_entry_order(
    tmp_path,
) -> None:
    BenchmarkManifest, BenchmarkManifestEntry, load_manifest = _manifest_api()
    first = BenchmarkManifestEntry.model_validate(_entry_payload())
    second = BenchmarkManifestEntry.model_validate(
        _entry_payload(
            sample_id="e621-human-0001",
            source_corpus="e621",
            image_sha256="b" * 64,
        )
    )
    manifest = BenchmarkManifest(entries=(second, first))

    manifest_path = tmp_path / "anime-detectors-v1.jsonl"
    manifest_path.write_text(
        "\n".join(
            json.dumps(payload)
            for payload in (_entry_payload(), _entry_payload(
                sample_id="e621-human-0001",
                source_corpus="e621",
                image_sha256="b" * 64,
            ))
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_manifest(manifest_path)

    expected = hashlib.sha256(
        b"".join(
            json.dumps(
                entry.model_dump(mode="json"),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
            for entry in sorted((first, second), key=lambda item: item.sample_id)
        )
    ).hexdigest()
    assert manifest.canonical_sha256 == expected
    assert loaded.canonical_sha256 == expected
    assert [entry.sample_id for entry in loaded.entries] == [
        "danbooru-human-0001",
        "e621-human-0001",
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload.update(source_corpus="mixed"),
            "source_corpus",
        ),
        (
            lambda payload: payload.update(image_path="../source.png"),
            "safe relative",
        ),
        (
            lambda payload: payload.update(image_sha256="not-a-sha256"),
            "normalized to lowercase",
        ),
        (
            lambda payload: payload["watermark_labels"].update(unexpected=_annotation("present")),
            "Extra inputs",
        ),
    ],
)
def test_manifest_rejects_invalid_corpus_path_hash_and_label_keys(
    mutate,
    message: str,
) -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        BenchmarkManifestEntry.model_validate(payload)


def test_manifest_rejects_ground_truth_claim_from_unknown_label_source() -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    payload["ai_origin"]["label_trust"] = "unknown"

    with pytest.raises(ValidationError, match="unknown value requires label_trust"):
        BenchmarkManifestEntry.model_validate(payload)


def test_manifest_rejects_unknown_label_value_with_trusted_label_source() -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    payload["ai_origin"] = _annotation("unknown")

    with pytest.raises(ValidationError, match="unknown value requires label_trust"):
        BenchmarkManifestEntry.model_validate(payload)


def test_manifest_rejects_mutually_exclusive_human_and_ai_strata() -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    payload["strata"] = ["human_anime", "ai_anime"]

    with pytest.raises(ValidationError, match="human_anime and ai_anime are mutually exclusive"):
        BenchmarkManifestEntry.model_validate(payload)


@pytest.mark.parametrize(
    ("stratum", "origin", "trust", "message"),
    [
        ("human_anime", "unknown", "unknown", "human_anime requires trusted ai_origin=human"),
        ("ai_anime", "human", "trusted", "ai_anime requires trusted ai_origin=ai"),
    ],
)
def test_manifest_requires_trusted_origin_for_anime_strata(
    stratum: str,
    origin: str,
    trust: str,
    message: str,
) -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    payload["strata"] = [stratum]
    payload["ai_origin"] = _annotation(origin, trust=trust)

    with pytest.raises(ValidationError, match=message):
        BenchmarkManifestEntry.model_validate(payload)


@pytest.mark.parametrize(
    ("stratum", "label_key", "message"),
    [
        ("signature", "signature", "signature requires trusted signature=present"),
        ("watermark", "watermark", "watermark requires trusted watermark=present"),
        ("ordinary_text", "text", "ordinary_text requires trusted text=present"),
    ],
)
def test_manifest_requires_trusted_present_evidence_for_strata(
    stratum: str,
    label_key: str,
    message: str,
) -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    payload["strata"] = ["human_anime", stratum]
    payload["watermark_labels"][label_key] = _annotation("absent")

    with pytest.raises(ValidationError, match=message):
        BenchmarkManifestEntry.model_validate(payload)


def test_manifest_requires_trusted_absent_watermark_for_hard_negative() -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    payload["strata"] = ["human_anime", "no_watermark_hard_negative"]
    payload["watermark_labels"]["watermark"] = _annotation("present")

    with pytest.raises(
        ValidationError,
        match="no_watermark_hard_negative requires trusted watermark=absent",
    ):
        BenchmarkManifestEntry.model_validate(payload)


def test_manifest_requires_parent_hash_for_compressed_or_resized_stratum() -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    payload["strata"] = ["human_anime", "compressed_or_resized"]

    with pytest.raises(ValidationError, match="compressed_or_resized requires derivation"):
        BenchmarkManifestEntry.model_validate(payload)


def test_manifest_rejects_derived_image_with_its_parent_hash() -> None:
    _, BenchmarkManifestEntry, _ = _manifest_api()
    payload = _entry_payload()
    payload["strata"] = ["human_anime", "compressed_or_resized"]
    payload["derivation"] = {
        "parent_image_sha256": payload["image_sha256"],
        "kind": "compression",
        "parameters": {"quality": 80},
    }

    with pytest.raises(
        ValidationError,
        match="derived image_sha256 must differ from parent_image_sha256",
    ):
        BenchmarkManifestEntry.model_validate(payload)


def test_manifest_rejects_duplicate_image_hashes_across_sample_ids() -> None:
    BenchmarkManifest, BenchmarkManifestEntry, _ = _manifest_api()
    first = BenchmarkManifestEntry.model_validate(_entry_payload())
    second = BenchmarkManifestEntry.model_validate(
        _entry_payload(sample_id="danbooru-human-0002")
    )

    with pytest.raises(ValidationError, match="image_sha256 values must be unique"):
        BenchmarkManifest(entries=(first, second))


def test_benchmark_hashes_accept_case_insensitive_input_and_normalize_lowercase() -> None:
    BenchmarkManifest, BenchmarkManifestEntry, _ = _manifest_api()
    BenchmarkRunConfig, validate_run_config_manifest = _run_config_api()
    payload = _entry_payload(image_sha256="A" * 64)
    payload["strata"] = ["human_anime", "compressed_or_resized"]
    payload["derivation"] = {
        "parent_image_sha256": "B" * 64,
        "kind": "resize",
        "parameters": {"width": 512},
    }
    entry = BenchmarkManifestEntry.model_validate(payload)
    manifest = BenchmarkManifest(entries=(entry,))
    config = BenchmarkRunConfig.model_validate(
        {
            "schema_version": "detector-benchmark-run/v1",
            "manifest_sha256": manifest.canonical_sha256.upper(),
            "seed": 17,
            "review_top_k": 7,
            "device": "cpu",
            "precision": "float32",
            "offline": True,
            "report_only": True,
            "models": [
                {
                    "model_id": "universal_fake_detector_head",
                    "role": "baseline",
                    "source_kind": "github",
                    "source_repository": "WisconsinAIVision/UniversalFakeDetect",
                    "revision": "76a0e3e60a8a06458707a625d269ba815a2e5919",
                    "artifact_sha256": ["C" * 64],
                    "declared_license": "MIT",
                    "remote_code_allowed": False,
                }
            ],
        }
    )

    validate_run_config_manifest(config, manifest)
    assert entry.image_sha256 == "a" * 64
    assert entry.derivation is not None
    assert entry.derivation.parent_image_sha256 == "b" * 64
    assert config.manifest_sha256 == manifest.canonical_sha256
    assert config.models[0].artifact_sha256 == ("c" * 64,)


def test_run_config_is_report_only_pinned_and_bound_to_manifest_hash() -> None:
    BenchmarkManifest, BenchmarkManifestEntry, _ = _manifest_api()
    BenchmarkRunConfig, validate_run_config_manifest = _run_config_api()
    entry = BenchmarkManifestEntry.model_validate(_entry_payload())
    manifest = BenchmarkManifest(entries=(entry,))
    config_payload = {
        "schema_version": "detector-benchmark-run/v1",
        "manifest_sha256": manifest.canonical_sha256,
        "seed": 17,
        "review_top_k": 7,
        "device": "cpu",
        "precision": "float32",
        "offline": True,
        "report_only": True,
        "models": [
            {
                "model_id": "universal_fake_detector_head",
                "role": "baseline",
                "source_kind": "github",
                "source_repository": "WisconsinAIVision/UniversalFakeDetect",
                "revision": "76a0e3e60a8a06458707a625d269ba815a2e5919",
                "artifact_sha256": ["c" * 64],
                "declared_license": "MIT",
                "remote_code_allowed": False,
            }
        ],
    }
    config = BenchmarkRunConfig.model_validate(config_payload)

    validate_run_config_manifest(config, manifest)
    with pytest.raises(ValidationError, match="Extra inputs"):
        BenchmarkRunConfig.model_validate({**config_payload, "score_threshold": "forbidden"})
    mismatched = config.model_copy(update={"manifest_sha256": "d" * 64})
    with pytest.raises(ValueError, match="does not match manifest"):
        validate_run_config_manifest(mismatched, manifest)
