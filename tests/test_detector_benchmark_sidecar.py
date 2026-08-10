from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from dataset_audit_studio.benchmarks.detector_preflight import DetectorPreflightReport
from dataset_audit_studio.benchmarks.manifest import BenchmarkManifest, BenchmarkManifestEntry
from dataset_audit_studio.benchmarks.run_config import BenchmarkRunConfigV2
from PIL import Image
from pydantic import ValidationError


def _api() -> dict[str, Any]:
    try:
        import dataset_audit_studio.benchmarks.sidecar as sidecar
        from dataset_audit_studio.benchmarks.sidecar import (
            BATCH_SCHEMA_VERSION,
            FAILURE_SCHEMA_VERSION,
            OCR_SCHEMA_VERSION,
            RUN_SCHEMA_VERSION,
            SCORE_SCHEMA_VERSION,
            SUPPORTED_DETECTOR_MODEL_IDS,
            WD14_TAG_SCORE_LABELS,
            BenchmarkOcrRecord,
            BenchmarkSidecarBatch,
            BenchmarkSidecarFailure,
            BenchmarkSidecarRun,
            canonical_score_sha256,
            validate_benchmark_inputs,
            validate_detector_batch_outputs,
            validate_sidecar_outputs,
        )
    except ImportError as error:
        pytest.fail(f"Benchmark sidecar API is not implemented: {error}")
    return {
        "BATCH_SCHEMA_VERSION": BATCH_SCHEMA_VERSION,
        "FAILURE_SCHEMA_VERSION": FAILURE_SCHEMA_VERSION,
        "OCR_SCHEMA_VERSION": OCR_SCHEMA_VERSION,
        "RUN_SCHEMA_VERSION": RUN_SCHEMA_VERSION,
        "SCORE_SCHEMA_VERSION": SCORE_SCHEMA_VERSION,
        "BenchmarkOcrRecord": BenchmarkOcrRecord,
        "BenchmarkSidecarBatch": BenchmarkSidecarBatch,
        "BenchmarkSidecarFailure": BenchmarkSidecarFailure,
        "BenchmarkSidecarRun": BenchmarkSidecarRun,
        "canonical_score_sha256": canonical_score_sha256,
        "SUPPORTED_DETECTOR_MODEL_IDS": SUPPORTED_DETECTOR_MODEL_IDS,
        "WD14_TAG_SCORE_LABELS": WD14_TAG_SCORE_LABELS,
        "module": sidecar,
        "validate_benchmark_inputs": validate_benchmark_inputs,
        "validate_detector_batch_outputs": validate_detector_batch_outputs,
        "validate_sidecar_outputs": validate_sidecar_outputs,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _annotation(value: str, *, trust: str = "trusted") -> dict[str, str]:
    return {
        "value": value,
        "label_source": "manual:benchmark-review-v1",
        "label_trust": trust,
    }


def _entry(image_path: str, image_sha256: str, *, sample_id: str) -> BenchmarkManifestEntry:
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


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (5, 3), color) as image:
        image.save(path)


def _manifest_with_images(tmp_path: Path) -> tuple[Path, BenchmarkManifest]:
    images_root = tmp_path / "images"
    first_path = images_root / "nested" / "sample-001.png"
    second_path = images_root / "nested" / "sample-002.png"
    _write_image(first_path, (16, 32, 64))
    _write_image(second_path, (64, 32, 16))
    first = _entry("nested/sample-001.png", _sha256(first_path), sample_id="sample-001")
    second = _entry("nested/sample-002.png", _sha256(second_path), sample_id="sample-002")
    return images_root, BenchmarkManifest(entries=(second, first))


def _model_reference(
    model_id: str,
    *,
    role: str,
    artifact_character: str,
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
        "batch_size": 2,
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
    auxiliary_ocr: bool = False,
) -> BenchmarkRunConfigV2:
    models = [
        _model_reference(
            model_id,
            role="baseline" if index < 2 else "candidate",
            artifact_character=chr(ord("b") + index),
        )
        for index, model_id in enumerate(model_ids)
    ]
    payload: dict[str, Any] = {
        "schema_version": "detector-benchmark-run/v2",
        "manifest_sha256": manifest.canonical_sha256,
        "seed": 7,
        "review_top_k": 5,
        "device": "cpu",
        "precision": "float32",
        "offline": True,
        "report_only": True,
        "warmup_batches": 0,
        "timing_repeats": 1,
        "models": models,
    }
    if auxiliary_ocr:
        payload["auxiliary_ocr"] = {
            "detector": {
                **_model_reference(
                    "ppocrv5_server_det",
                    role="candidate",
                    artifact_character="e",
                ),
                "batch_size": 1,
            },
            "recognizer": {
                **_model_reference(
                    "ppocrv5_server_rec",
                    role="candidate",
                    artifact_character="f",
                ),
                "batch_size": 1,
            },
        }
        payload["auxiliary_ocr"]["detector"].pop("role")
        payload["auxiliary_ocr"]["recognizer"].pop("role")
    return BenchmarkRunConfigV2.model_validate(payload)


def _ready_reports(config: BenchmarkRunConfigV2) -> tuple[DetectorPreflightReport, ...]:
    model_ids = [model.model_id for model in config.models]
    if config.auxiliary_ocr is not None:
        model_ids.extend(
            (
                config.auxiliary_ocr.detector.model_id,
                config.auxiliary_ocr.recognizer.model_id,
            )
        )
    return tuple(
        DetectorPreflightReport(
            model_id=model_id,
            status="ready",
            root=Path("."),
            files=(),
            errors=(),
            run_config_artifacts="matched",
        )
        for model_id in model_ids
    )


def _raw_output(api: dict[str, Any], model_id: str) -> dict[str, Any]:
    if model_id in {"universal_fake_detector_head", "commfor_model_384"}:
        return {"raw_sigmoid_score": 0.25}
    if model_id == "watermark_siglip2":
        return {
            "raw_softmax_label_score": 0.75,
            "raw_softmax_label_scores": {"Watermark": 0.75, "Clean": 0.25},
        }
    if model_id == "wd14_eva02_large_v3":
        return {
            "raw_sigmoid_tag_scores": {
                tag: 0.5 for tag in api["WD14_TAG_SCORE_LABELS"]
            }
        }
    raise AssertionError(f"Unexpected test model id: {model_id}")


def _sidecar_run(
    api: dict[str, Any], config: BenchmarkRunConfigV2, scores: list[Any]
) -> Any:
    return api["BenchmarkSidecarRun"].model_validate(
        {
            "schema_version": api["RUN_SCHEMA_VERSION"],
            "run_config_schema_version": config.schema_version,
            "run_config_sha256": config.canonical_sha256,
            "manifest_sha256": config.manifest_sha256,
            "canonical_score_sha256": api["canonical_score_sha256"](scores),
            "detector_model_ids": [model.model_id for model in config.models],
            "detector_provenance": [
                {
                    "model_id": model.model_id,
                    "assets": [
                        {
                            "asset_id": model.model_id,
                            "role": "model",
                            "files": [
                                {
                                    "path": "model.safetensors",
                                    "sha256": model.artifact_sha256[0],
                                    "size_bytes": 1,
                                }
                            ],
                        }
                    ],
                    "preprocessing": {
                        "source": "tests/fake-preprocessor",
                        "revision": "test-v1",
                    },
                }
                for model in config.models
            ],
            "auxiliary_ocr_enabled": config.auxiliary_ocr is not None,
            "auxiliary_ocr_provenance": (
                {
                    "assets": [
                        {
                            "asset_id": config.auxiliary_ocr.detector.model_id,
                            "role": "model",
                            "files": [
                                {
                                    "path": "model.safetensors",
                                    "sha256": config.auxiliary_ocr.detector.artifact_sha256[0],
                                    "size_bytes": 1,
                                }
                            ],
                        },
                        {
                            "asset_id": config.auxiliary_ocr.recognizer.model_id,
                            "role": "model",
                            "files": [
                                {
                                    "path": "model.safetensors",
                                    "sha256": config.auxiliary_ocr.recognizer.artifact_sha256[0],
                                    "size_bytes": 1,
                                }
                            ],
                        },
                    ],
                    "preprocessing": {
                        "source": "tests/fake-ocr-preprocessor",
                        "revision": "test-v1",
                    },
                }
                if config.auxiliary_ocr is not None
                else None
            ),
            "report_only": True,
            "requested_device": config.device,
            "actual_device": "cpu",
            "requested_precision": config.precision,
            "actual_precision": config.precision,
            "software": {"runtime": "pytest"},
            "hardware": {"processor": "fake"},
        }
    )


def _complete_outputs(
    api: dict[str, Any],
    config: BenchmarkRunConfigV2,
    sample_ids: tuple[str, ...],
) -> tuple[list[Any], list[Any]]:
    batches: list[Any] = []
    scores: list[Any] = []
    for index, model in enumerate(config.models, start=1):
        batch_id = f"batch-{index:03d}"
        batches.append(
            api["BenchmarkSidecarBatch"].model_validate(
                {
                    "schema_version": api["BATCH_SCHEMA_VERSION"],
                    "batch_id": batch_id,
                    "model_id": model.model_id,
                    "sample_ids": list(sample_ids),
                    "repeat": 0,
                    "batch_size": len(sample_ids),
                    "end_to_end_duration_ms": 1.25,
                    "memory_measurement_supported": False,
                    "cuda_memory": None,
                }
            )
        )
        scores.extend(
            api["validate_detector_batch_outputs"](
                model_id=model.model_id,
                batch_id=batch_id,
                sample_ids=sample_ids,
                outputs=tuple(_raw_output(api, model.model_id) for _ in sample_ids),
            )
        )
    return batches, scores


def test_input_validation_requires_ready_matched_preflight_before_image_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(manifest, model_ids=("universal_fake_detector_head",))
    reports = list(_ready_reports(config))
    reports[0] = replace(reports[0], status="missing")

    def fail_hash(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("image validation ran before preflight completed")

    monkeypatch.setattr(api["module"], "_sha256_file", fail_hash)
    with pytest.raises(ValueError, match="preflight report"):
        api["validate_benchmark_inputs"](
            manifest=manifest,
            run_config=config,
            preflight_reports=tuple(reports),
            images_root=images_root,
        )

    unmatched = replace(_ready_reports(config)[0], run_config_artifacts="not_requested")
    with pytest.raises(ValueError, match="artifact snapshot"):
        api["validate_benchmark_inputs"](
            manifest=manifest,
            run_config=config,
            preflight_reports=(unmatched,),
            images_root=images_root,
        )

    two_model_config = _config(
        manifest,
        model_ids=("universal_fake_detector_head", "commfor_model_384"),
    )
    with pytest.raises(ValueError, match="configured model identities"):
        api["validate_benchmark_inputs"](
            manifest=manifest,
            run_config=two_model_config,
            preflight_reports=_ready_reports(two_model_config)[:1],
            images_root=images_root,
        )


def test_input_validation_returns_safe_images_in_stable_sample_id_order(tmp_path: Path) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(manifest, model_ids=("universal_fake_detector_head",))

    validated = api["validate_benchmark_inputs"](
        manifest=manifest,
        run_config=config,
        preflight_reports=_ready_reports(config),
        images_root=images_root,
    )

    assert [sample.entry.sample_id for sample in validated] == ["sample-001", "sample-002"]
    assert [sample.path.relative_to(images_root).as_posix() for sample in validated] == [
        "nested/sample-001.png",
        "nested/sample-002.png",
    ]


def test_input_validation_rejects_hash_mismatch_and_undecodable_file(tmp_path: Path) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    wrong_hash = BenchmarkManifest(
        entries=(manifest.entries[0].model_copy(update={"image_sha256": "f" * 64}),)
    )
    wrong_hash_config = _config(wrong_hash, model_ids=("universal_fake_detector_head",))

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        api["validate_benchmark_inputs"](
            manifest=wrong_hash,
            run_config=wrong_hash_config,
            preflight_reports=_ready_reports(wrong_hash_config),
            images_root=images_root,
        )

    corrupt_path = images_root / "nested" / "corrupt.png"
    corrupt_path.write_bytes(b"not an image")
    corrupt = BenchmarkManifest(
        entries=(_entry("nested/corrupt.png", _sha256(corrupt_path), sample_id="corrupt"),)
    )
    corrupt_config = _config(corrupt, model_ids=("universal_fake_detector_head",))

    with pytest.raises(ValueError, match="cannot be safely decoded"):
        api["validate_benchmark_inputs"](
            manifest=corrupt,
            run_config=corrupt_config,
            preflight_reports=_ready_reports(corrupt_config),
            images_root=images_root,
        )


def test_input_validation_rejects_missing_and_nonregular_image_paths(tmp_path: Path) -> None:
    api = _api()
    images_root, _ = _manifest_with_images(tmp_path)
    missing = BenchmarkManifest(
        entries=(_entry("nested/missing.png", "f" * 64, sample_id="missing"),)
    )
    missing_config = _config(missing, model_ids=("universal_fake_detector_head",))

    with pytest.raises(ValueError, match="image is missing"):
        api["validate_benchmark_inputs"](
            manifest=missing,
            run_config=missing_config,
            preflight_reports=_ready_reports(missing_config),
            images_root=images_root,
        )

    directory = images_root / "nested" / "not-an-image"
    directory.mkdir()
    nonregular = BenchmarkManifest(
        entries=(_entry("nested/not-an-image", "f" * 64, sample_id="directory"),)
    )
    nonregular_config = _config(nonregular, model_ids=("universal_fake_detector_head",))

    with pytest.raises(ValueError, match="regular file"):
        api["validate_benchmark_inputs"](
            manifest=nonregular,
            run_config=nonregular_config,
            preflight_reports=_ready_reports(nonregular_config),
            images_root=images_root,
        )


def test_input_validation_rejects_symlinked_and_root_escaping_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    images_root = tmp_path / "images"
    direct_link = images_root / "direct-link.png"
    _write_image(direct_link, (12, 34, 56))
    direct_manifest = BenchmarkManifest(
        entries=(
            _entry("direct-link.png", _sha256(direct_link), sample_id="direct-link"),
        )
    )
    direct_config = _config(direct_manifest, model_ids=("universal_fake_detector_head",))
    original_is_symlink = Path.is_symlink

    def direct_link_is_symlink(path: Path) -> bool:
        return path == direct_link or original_is_symlink(path)

    with monkeypatch.context() as context:
        context.setattr(Path, "is_symlink", direct_link_is_symlink)
        with pytest.raises(ValueError, match="symbolic link"):
            api["validate_benchmark_inputs"](
                manifest=direct_manifest,
                run_config=direct_config,
                preflight_reports=_ready_reports(direct_config),
                images_root=images_root,
            )

    escaped_image = tmp_path / "outside" / "escaped.png"
    _write_image(escaped_image, (56, 34, 12))
    in_root_image = images_root / "nested-link" / "escaped.png"
    _write_image(in_root_image, (12, 34, 56))
    escaping_manifest = BenchmarkManifest(
        entries=(
            _entry("nested-link/escaped.png", _sha256(escaped_image), sample_id="escaped"),
        )
    )
    escaping_config = _config(escaping_manifest, model_ids=("universal_fake_detector_head",))
    original_resolve = Path.resolve

    def resolve_outside_root(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path == images_root / "nested-link" / "escaped.png":
            return original_resolve(escaped_image, *args, **kwargs)
        return original_resolve(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "resolve", resolve_outside_root)
        with pytest.raises(ValueError, match="escapes images_root"):
            api["validate_benchmark_inputs"](
                manifest=escaping_manifest,
                run_config=escaping_config,
                preflight_reports=_ready_reports(escaping_config),
                images_root=images_root,
            )


@pytest.mark.parametrize(
    "model_id",
    (
        "universal_fake_detector_head",
        "commfor_model_384",
        "watermark_siglip2",
        "wd14_eva02_large_v3",
    ),
)
def test_fixed_detector_output_schemas_are_versioned_and_exact(model_id: str) -> None:
    api = _api()
    records = api["validate_detector_batch_outputs"](
        model_id=model_id,
        batch_id="batch-001",
        sample_ids=("sample-001",),
        outputs=(_raw_output(api, model_id),),
    )

    assert len(records) == 1
    assert records[0].schema_version == api["SCORE_SCHEMA_VERSION"]
    assert records[0].model_id == model_id
    assert records[0].sample_id == "sample-001"


def test_detector_output_validation_rejects_count_key_and_nonfinite_errors() -> None:
    api = _api()
    with pytest.raises(ValueError, match="output count"):
        api["validate_detector_batch_outputs"](
            model_id="universal_fake_detector_head",
            batch_id="batch-001",
            sample_ids=("sample-001", "sample-002"),
            outputs=(_raw_output(api, "universal_fake_detector_head"),),
        )
    with pytest.raises(ValueError, match="exactly"):
        api["validate_detector_batch_outputs"](
            model_id="commfor_model_384",
            batch_id="batch-001",
            sample_ids=("sample-001",),
            outputs=({"raw_sigmoid_score": 0.5, "unexpected": 1.0},),
        )
    with pytest.raises(ValidationError, match="finite"):
        api["validate_detector_batch_outputs"](
            model_id="watermark_siglip2",
            batch_id="batch-001",
            sample_ids=("sample-001",),
            outputs=(
                {
                    "raw_softmax_label_score": float("nan"),
                    "raw_softmax_label_scores": {"Watermark": 0.5},
                },
            ),
        )
    with pytest.raises(ValidationError, match="finite"):
        api["validate_detector_batch_outputs"](
            model_id="commfor_model_384",
            batch_id="batch-001",
            sample_ids=("sample-001",),
            outputs=({"raw_sigmoid_score": float("inf")},),
        )
    with pytest.raises(ValidationError, match="approved"):
        api["validate_detector_batch_outputs"](
            model_id="wd14_eva02_large_v3",
            batch_id="batch-001",
            sample_ids=("sample-001",),
            outputs=(
                {"raw_sigmoid_tag_scores": {"watermark": 0.5}},
            ),
        )


def test_sidecar_contracts_bind_v2_run_and_validate_all_scores_and_batches(
    tmp_path: Path,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(manifest)
    inputs = api["validate_benchmark_inputs"](
        manifest=manifest,
        run_config=config,
        preflight_reports=_ready_reports(config),
        images_root=images_root,
    )
    sample_ids = tuple(sample.entry.sample_id for sample in inputs)
    batches, scores = _complete_outputs(api, config, sample_ids)

    api["validate_sidecar_outputs"](
        run=_sidecar_run(api, config, scores),
        run_config=config,
        validated_inputs=inputs,
        batches=batches,
        scores=scores,
        ocr_records=None,
    )
    assert set(api["SUPPORTED_DETECTOR_MODEL_IDS"]) == {
        "universal_fake_detector_head",
        "commfor_model_384",
        "watermark_siglip2",
        "wd14_eva02_large_v3",
    }
    assert api["BenchmarkSidecarFailure"].model_validate(
        {
            "schema_version": api["FAILURE_SCHEMA_VERSION"],
            "stage": "output_validation",
            "message": "synthetic validation failure",
            "model_id": "wd14_eva02_large_v3",
            "batch_id": "batch-001",
            "exception_type": "ValueError",
            "completed_score_count": 3,
            "completed_batch_count": 1,
        }
    ).model_id == "wd14_eva02_large_v3"


def test_sidecar_output_validation_rejects_mismatched_score_and_batch_membership(
    tmp_path: Path,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(manifest, model_ids=("universal_fake_detector_head",))
    inputs = api["validate_benchmark_inputs"](
        manifest=manifest,
        run_config=config,
        preflight_reports=_ready_reports(config),
        images_root=images_root,
    )
    sample_ids = tuple(sample.entry.sample_id for sample in inputs)
    batches, scores = _complete_outputs(api, config, sample_ids)
    invalid_batch = batches[0].model_copy(update={"sample_ids": (sample_ids[0],)})

    with pytest.raises(ValueError, match="sample_ids"):
        api["validate_sidecar_outputs"](
            run=_sidecar_run(api, config, scores),
            run_config=config,
            validated_inputs=inputs,
            batches=[invalid_batch],
            scores=scores,
            ocr_records=None,
        )


def test_ocr_records_remain_separate_and_require_auxiliary_ocr_configuration(
    tmp_path: Path,
) -> None:
    api = _api()
    images_root, manifest = _manifest_with_images(tmp_path)
    config = _config(
        manifest,
        model_ids=("universal_fake_detector_head",),
        auxiliary_ocr=True,
    )
    inputs = api["validate_benchmark_inputs"](
        manifest=manifest,
        run_config=config,
        preflight_reports=_ready_reports(config),
        images_root=images_root,
    )
    sample_ids = tuple(sample.entry.sample_id for sample in inputs)
    batches, scores = _complete_outputs(api, config, sample_ids)
    ocr_records = [
        api["BenchmarkOcrRecord"].model_validate(
            {
                "schema_version": api["OCR_SCHEMA_VERSION"],
                "sample_id": sample_id,
                "detector_model_id": "ppocrv5_server_det",
                "recognizer_model_id": "ppocrv5_server_rec",
                "regions": [
                    {
                        "box": [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]],
                        "detection_score": 0.8,
                        "recognition_score": 0.7,
                        "text": "sample text",
                    }
                ],
                "text_area_ratio": 0.1,
            }
        )
        for sample_id in sample_ids
    ]

    api["validate_sidecar_outputs"](
        run=_sidecar_run(api, config, scores),
        run_config=config,
        validated_inputs=inputs,
        batches=batches,
        scores=scores,
        ocr_records=ocr_records,
    )

    no_ocr_config = _config(manifest, model_ids=("universal_fake_detector_head",))
    no_ocr_inputs = api["validate_benchmark_inputs"](
        manifest=manifest,
        run_config=no_ocr_config,
        preflight_reports=_ready_reports(no_ocr_config),
        images_root=images_root,
    )
    no_ocr_batches, no_ocr_scores = _complete_outputs(api, no_ocr_config, sample_ids)
    with pytest.raises(ValueError, match="auxiliary_ocr"):
        api["validate_sidecar_outputs"](
            run=_sidecar_run(api, no_ocr_config, no_ocr_scores),
            run_config=no_ocr_config,
            validated_inputs=no_ocr_inputs,
            batches=no_ocr_batches,
            scores=no_ocr_scores,
            ocr_records=ocr_records,
        )
