from __future__ import annotations

import copy
import tomllib
from pathlib import Path

import pytest
from dataset_audit_studio.model_adapters.registry import DEFAULT_REGISTRY
from dataset_audit_studio.model_adapters.types import RegistryDocument
from pydantic import ValidationError


def test_default_registry_is_complete_pinned_and_code_free() -> None:
    registry = DEFAULT_REGISTRY
    assert len(registry.all()) == 13
    assert sum(model.total_size for model in registry.all()) == 11_877_569_054
    assert registry.document.registry_version == "2026-08-10.1"
    assert len(registry.digest) == 64

    for model in registry.all():
        assert model.source.remote_code_allowed is False
        if model.source.kind in {"github", "huggingface"}:
            assert model.source.revision is not None
            assert len(model.source.revision) == 40
            assert model.source.revision != "main"
        for file in model.files:
            assert len(file.sha256) == 64
            assert file.size > 0
            assert not file.path.casefold().endswith((".py", ".exe", ".dll", ".pyd"))
            assert "/main/" not in (file.url or "").casefold()

    aesthetic = registry.get("aesthetic_lse14_5k")
    assert aesthetic.files[0].sha256 == (
        "f8020306baae1de94b085647121ed368e07c9cf36e1d4eb92e4f3372f3624faa"
    )
    assert aesthetic.replaceable is True
    assert aesthetic.replacement_schema == "lse14_fusion_multitask_v1"
    assert [model.id for model in registry.dependency_order(aesthetic.id)] == [
        "jtp3_hydra",
        "openai_clip_vit_l14",
        "waifu_scorer_v3",
        "aesthetic_lse14_5k",
    ]

    community = registry.get("community_forensics_model_384")
    assert community.source.repository == "OwensLab/commfor-model-384"
    assert community.source.revision == "6076002bf0d9dd37537f965ee2f06f826c333b61"
    assert community.loader == "community_forensics_vit_small_384_v1"
    assert community.dependencies == ()
    assert community.files[0].path == "model.safetensors"
    assert community.files[0].size == 87_262_324
    assert community.files[0].sha256 == (
        "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387"
    )

    adapter_root = Path(__file__).parents[1] / "backend" / "dataset_audit_studio" / "model_adapters"
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8") for path in adapter_root.glob("*.py")
    )
    assert "trust_remote_code=True" not in runtime_source
    assert "snapshot_download" not in runtime_source


def test_huggingface_urls_are_derived_from_full_revision() -> None:
    model = DEFAULT_REGISTRY.get("ppocrv5_server_det")
    url = DEFAULT_REGISTRY.file_url(model, model.files[-1])
    assert url == (
        "https://huggingface.co/PaddlePaddle/PP-OCRv5_server_det_safetensors/resolve/"
        "cbea9f3c3254c6ff7b0016cfbf90549e1ad4c5bb/model.safetensors"
    )
    assert "ocr_pipeline.py" not in {file.path for file in model.files}


def test_legacy_tagger_support_is_not_shipped() -> None:
    adapter_root = Path(__file__).parents[1] / "backend" / "dataset_audit_studio" / "model_adapters"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in adapter_root.glob("*.py")
    ).casefold()
    legacy_name = "wd" + "14"

    assert legacy_name not in source
    registry = (adapter_root / "registry.json").read_text(encoding="utf-8").casefold()
    assert legacy_name not in registry


def test_legacy_tagger_runtime_is_not_a_project_dependency() -> None:
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    dependencies = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]

    assert all("onnxruntime" not in dependency.casefold() for dependency in dependencies)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["models"][0]["source"].update(revision="main"),
            "40-character revision",
        ),
        (
            lambda payload: payload["models"][0]["files"][0].update(path="loader.py"),
            "forbidden",
        ),
        (
            lambda payload: payload["models"][0]["files"][0].update(path=".hidden.json"),
            "hidden",
        ),
        (
            lambda payload: payload["models"][3]["files"][0].update(
                url="https://127.0.0.1/model.pt"
            ),
            "trusted host",
        ),
        (
            lambda payload: payload["models"][1].update(
                dependencies=["aesthetic_lse14_5k"]
            ),
            "cycle",
        ),
    ],
)
def test_registry_rejects_unpinned_executable_and_cyclic_specs(mutate, message: str) -> None:
    payload = copy.deepcopy(DEFAULT_REGISTRY.document.model_dump(mode="json"))
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        RegistryDocument.model_validate(payload)
