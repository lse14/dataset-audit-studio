import hashlib
import json
from pathlib import Path, PurePosixPath

from dataset_audit_studio.components.dataset_export.annotations import plan_paired_annotations
from dataset_audit_studio.components.dataset_export.contracts import DatasetSummary, PlannedFile
from dataset_audit_studio.components.dataset_export.planner import _input_digest, build_export_plan
from dataset_audit_studio.core.dataset_artifacts import (
    DatasetSample,
    DatasetSlice,
    DatasetWorkspace,
)


def test_plan_paired_annotations_preserves_txt_and_json_bytes(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    image = source_root / "artist" / "sample.webp"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    (image.with_suffix(".txt")).write_bytes(b"tag1\r\ntag2\r\n")
    (image.with_suffix(".json")).write_bytes(b'{"prompt":"tag1"}')

    files = plan_paired_annotations(
        image_source=image,
        source_root=source_root,
        destination_image=PurePosixPath("stage1/512/artist/sample.png"),
    )

    assert [(file.destination_relative, file.kind) for file in files] == [
        ("stage1/512/artist/sample.txt", "source_annotation"),
        ("stage1/512/artist/sample.json", "source_annotation"),
    ]
    assert [file.source_path.read_bytes() for file in files if file.source_path] == [
        b"tag1\r\ntag2\r\n",
        b'{"prompt":"tag1"}',
    ]


def test_plan_paired_annotations_ignores_missing_sidecars(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    image = source_root / "sample.png"
    image.write_bytes(b"image")

    assert plan_paired_annotations(
        image_source=image,
        source_root=source_root,
        destination_image=PurePosixPath("stage1/512/sample.png"),
    ) == ()


def test_incremental_input_digest_matches_the_legacy_canonical_payload() -> None:
    files = (
        PlannedFile(
            destination_relative="stage1/512/sample.png",
            sha256="a" * 64,
            size_bytes=123,
            kind="source_image",
        ),
    )
    datasets = (
        DatasetSummary(
            stage=1,
            resolution=512,
            relative_root="stage1/512",
            file_count=1,
            byte_count=123,
        ),
    )
    legacy_payload = {
        "files": [
            [file.destination_relative, file.sha256, file.size_bytes, file.kind] for file in files
        ],
        "datasets": [summary.__dict__ for summary in datasets],
    }
    expected = hashlib.sha256(
        json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert _input_digest(files, datasets) == expected


def test_export_plan_counts_a_duplicate_selected_image_once(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    image = source_root / "sample.png"
    image.write_bytes(b"image")
    stat = image.stat()
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    sample = DatasetSample(
        sample_id="sample-1",
        relative_path="sample.png",
        artist_scope="",
        source_path=image,
        image_path=image,
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        source_sha256=digest,
        pixel_sha256=digest,
        export_requires_render=False,
    )

    plan = build_export_plan(
        DatasetWorkspace(
            samples=(sample,),
            datasets=(DatasetSlice(stage=1, resolution=512, sample_ids=(sample.sample_id,) * 2),),
        ),
        source_root=source_root,
        latents=None,
        keep_annotation_files=False,
    )

    assert plan.datasets[0].file_count == 1
    assert plan.datasets[0].byte_count == len(b"image")
