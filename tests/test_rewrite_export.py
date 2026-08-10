from pathlib import Path

import pytest
from dataset_audit_studio.export.rewrite import execute_rewrite, restore_latest_backup


def test_rewrite_backup_moves_and_restores_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    image = source / "artist" / "sample.png"
    image.parent.mkdir()
    image.write_bytes(b"image")
    annotation = image.with_suffix(".txt")
    annotation.write_text("tags", encoding="utf-8")
    latent = image.with_suffix(".npz")
    latent.write_bytes(b"latent")

    result = execute_rewrite(
        source,
        "f2a75e93-52a4-4483-90f9-bc2d9ee96376",
        (image, annotation, latent),
        backup_enabled=True,
    )

    backup = Path(str(result["backup_path"]))
    assert result["deleted_files"] == 3
    assert not image.exists()
    assert (backup / "artist" / "sample.png").read_bytes() == b"image"

    restored = restore_latest_backup(source, "f2a75e93-52a4-4483-90f9-bc2d9ee96376")

    assert restored["restored_files"] == 3
    assert image.read_bytes() == b"image"
    assert annotation.read_text(encoding="utf-8") == "tags"
    assert latent.read_bytes() == b"latent"


def test_rewrite_without_backup_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    image = source / "sample.png"
    image.write_bytes(b"image")

    with pytest.raises(ValueError, match="backup_enabled"):
        execute_rewrite(source, "task", (image,), backup_enabled=False)
    assert image.read_bytes() == b"image"
