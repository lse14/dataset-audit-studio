from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from dataset_audit_studio.components.dataset_export.contracts import (
    ExportPlan,
    PlannedFile,
)


def test_export_tree_publisher_stages_verifies_and_atomically_publishes(
    tmp_path: Path,
) -> None:
    from dataset_audit_studio.export.tree_publisher import ExportTreePublisher

    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "source.bin"
    source_bytes = b"source bytes\x00"
    source.write_bytes(source_bytes)
    content_bytes = b'{"caption":"inline"}\n'
    source_file = PlannedFile(
        destination_relative="images/source.bin",
        sha256=hashlib.sha256(source_bytes).hexdigest(),
        size_bytes=len(source_bytes),
        kind="source",
        source_path=source,
    )
    content_file = PlannedFile(
        destination_relative="metadata/caption.json",
        sha256=hashlib.sha256(content_bytes).hexdigest(),
        size_bytes=len(content_bytes),
        kind="content",
        content=content_bytes,
    )
    plan = ExportPlan(
        files=(source_file, content_file),
        datasets=(),
        latent_records=(),
        input_digest="test-input",
    )
    output_root = tmp_path / "output"
    staging_root = tmp_path / ".output.staging-test"
    publisher = ExportTreePublisher()

    publisher.validate_roots(source_root, output_root)
    publisher.prepare_directories(
        output_root,
        staging_root,
        plan,
        refuse_nonempty=True,
    )
    for file in plan.files:
        publisher.write_file(staging_root, file)
    publisher.verify_tree(staging_root, plan)

    content_path = staging_root / "metadata" / "caption.json"
    content_path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="Export file verification failed"):
        publisher.verify_tree(staging_root, plan)
    content_path.write_bytes(content_bytes)
    publisher.verify_tree(staging_root, plan)

    publisher.publish_tree(staging_root, output_root)

    assert not staging_root.exists()
    assert (output_root / "images" / "source.bin").read_bytes() == source_bytes
    assert (output_root / "metadata" / "caption.json").read_bytes() == content_bytes
    assert source.read_bytes() == source_bytes
    assert not tuple(output_root.rglob("*.part"))

    manifest = tmp_path / "manifest.json"
    manifest_bytes = b'{"schema":"export.dataset.v1"}\n'
    publisher.publish_bytes(manifest, manifest_bytes, temporary_label="manifest")

    assert manifest.read_bytes() == manifest_bytes
    assert not manifest.with_name(f"{manifest.name}.part").exists()
