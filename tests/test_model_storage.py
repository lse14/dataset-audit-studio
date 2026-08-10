from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from dataset_audit_studio.model_adapters.errors import (
    ModelIntegrityError,
    ModelRegistryError,
    ModelSchemaError,
)
from dataset_audit_studio.model_adapters.storage import ModelStorage
from dataset_audit_studio.model_adapters.validation import validate_lse14_replacement
from safetensors.numpy import save_file


def _write_lse14(
    path: Path,
    *,
    input_dim: int = 8273,
    hidden_dims: tuple[int, ...] = (4, 2),
    include_cls: bool = True,
    extra_tensor: bool = False,
    clip_model_name: str = "ViT-L-14",
) -> None:
    tensors: dict[str, np.ndarray] = {}
    previous = input_dim
    for index, hidden in enumerate(hidden_dims):
        layer = index * 4
        tensors[f"trunk.{layer}.weight"] = np.ones((previous,), dtype=np.float32)
        tensors[f"trunk.{layer}.bias"] = np.zeros((previous,), dtype=np.float32)
        tensors[f"trunk.{layer + 1}.weight"] = np.ones(
            (hidden, previous), dtype=np.float32
        )
        tensors[f"trunk.{layer + 1}.bias"] = np.zeros((hidden,), dtype=np.float32)
        previous = hidden
    tensors["reg_heads.aesthetic.weight"] = np.ones((1, previous), dtype=np.float32)
    tensors["reg_heads.aesthetic.bias"] = np.zeros((1,), dtype=np.float32)
    if include_cls:
        tensors["cls_head.weight"] = np.ones((1, previous), dtype=np.float32)
        tensors["cls_head.bias"] = np.zeros((1,), dtype=np.float32)
    if extra_tensor:
        tensors["remote.code"] = np.ones((1,), dtype=np.float32)
    metadata = {
        "format": "fusion_multitask_v1",
        "input_dim": str(input_dim),
        "hidden_dims_json": json.dumps(hidden_dims),
        "dropout": "0.2",
        "config_json": json.dumps(
            {
                "models": {
                    "jtp3_model_id": "RedRocket/JTP-3",
                    "waifu_clip_model_name": clip_model_name,
                    "waifu_clip_pretrained": "openai",
                    "include_waifu_score": True,
                },
                "training": {"target_dims": ["aesthetic"]},
            }
        ),
    }
    save_file(tensors, str(path), metadata=metadata)


def _snapshot(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def test_lse14_schema_supports_optional_domain_head(tmp_path: Path) -> None:
    with_domain = tmp_path / "with-domain.safetensors"
    aesthetic_only = tmp_path / "aesthetic-only.safetensors"
    _write_lse14(with_domain, include_cls=True)
    _write_lse14(aesthetic_only, include_cls=False)

    full = validate_lse14_replacement(with_domain)
    limited = validate_lse14_replacement(aesthetic_only)
    assert full.input_dim == 8273
    assert full.hidden_dims == (4, 2)
    assert full.has_in_domain_head is True
    assert limited.has_in_domain_head is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_dim": 1920},
        {"extra_tensor": True},
        {"clip_model_name": "ViT-B-32"},
    ],
)
def test_lse14_schema_rejects_incompatible_models(tmp_path: Path, kwargs: dict) -> None:
    path = tmp_path / "invalid.safetensors"
    _write_lse14(path, **kwargs)
    with pytest.raises(ModelSchemaError):
        validate_lse14_replacement(path)


def test_local_registration_copies_validates_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "trained.safetensors"
    _write_lse14(source)
    before = _snapshot(source)
    storage = ModelStorage(models_root=tmp_path / "project-models")

    registered = storage.register_local_replacement(
        base_model_id="aesthetic_lse14_5k",
        source_path=source,
        display_name="My local aesthetic model",
    )
    assert registered.installation_status == "ready"
    assert registered.is_custom is True
    assert registered.base_model_id == "aesthetic_lse14_5k"
    assert registered.revision == before[2]
    assert _snapshot(source) == before

    copied = storage.models_root.joinpath(*Path(registered.local_root).parts[1:])
    copied_file = copied / "model.safetensors"
    assert copied_file.is_file()
    assert copied_file.resolve() != source.resolve()
    assert _snapshot(copied_file)[2] == before[2]

    repeated = storage.register_local_replacement(
        base_model_id="aesthetic_lse14_5k",
        source_path=source,
        display_name="My local aesthetic model",
    )
    assert repeated.id == registered.id
    assert len(storage.custom_statuses()) == 1

    with copied_file.open("ab") as stream:
        stream.write(b"tamper")
    assert storage.custom_statuses()[0].installation_status == "corrupt"
    with pytest.raises(ModelIntegrityError):
        storage.verify_custom_model(registered.id)


def test_local_registration_rejects_relative_wrong_type_and_source_changes(
    tmp_path: Path,
) -> None:
    storage = ModelStorage(models_root=tmp_path / "models")
    with pytest.raises(ModelRegistryError, match="absolute"):
        storage.register_local_replacement(
            base_model_id="aesthetic_lse14_5k",
            source_path=Path("relative.safetensors"),
        )

    wrong = tmp_path / "weights.pt"
    wrong.write_bytes(b"not-safe")
    with pytest.raises(ModelRegistryError, match="safetensors"):
        storage.register_local_replacement(
            base_model_id="aesthetic_lse14_5k",
            source_path=wrong,
        )

    changing = tmp_path / "changing.safetensors"
    _write_lse14(changing)
    changed = False

    def mutate_source(_copied: int, _total: int) -> None:
        nonlocal changed
        if not changed:
            changed = True
            with changing.open("ab") as stream:
                stream.write(b"changed")

    with pytest.raises(ModelIntegrityError, match="changed"):
        storage.register_local_replacement(
            base_model_id="aesthetic_lse14_5k",
            source_path=changing,
            progress=mutate_source,
        )
