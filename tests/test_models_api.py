from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from dataset_audit_studio.main import create_app
from dataset_audit_studio.model_adapters.registry import DEFAULT_REGISTRY
from fastapi.testclient import TestClient
from safetensors.numpy import save_file


def _write_local_model(path: Path) -> None:
    tensors = {
        "trunk.0.weight": np.ones((8273,), dtype=np.float32),
        "trunk.0.bias": np.zeros((8273,), dtype=np.float32),
        "trunk.1.weight": np.ones((2, 8273), dtype=np.float32),
        "trunk.1.bias": np.zeros((2,), dtype=np.float32),
        "reg_heads.aesthetic.weight": np.ones((1, 2), dtype=np.float32),
        "reg_heads.aesthetic.bias": np.zeros((1,), dtype=np.float32),
    }
    save_file(
        tensors,
        str(path),
        metadata={
            "format": "fusion_multitask_v1",
            "input_dim": "8273",
            "hidden_dims_json": "[2]",
            "dropout": "0.1",
            "config_json": json.dumps(
                {
                    "models": {
                        "jtp3_model_id": "RedRocket/JTP-3",
                        "waifu_clip_model_name": "ViT-L-14",
                        "waifu_clip_pretrained": "openai",
                        "include_waifu_score": True,
                    },
                    "training": {"target_dims": ["aesthetic"]},
                }
            ),
        },
    )


def test_model_status_api_and_local_registration(tmp_path: Path) -> None:
    models_root = tmp_path / "models"
    source = tmp_path / "my-aesthetic.safetensors"
    _write_local_model(source)
    before = (
        source.stat().st_size,
        source.stat().st_mtime_ns,
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    app = create_app(
        database_path=tmp_path / "api.db",
        enforce_runtime=False,
        models_root=models_root,
    )

    with TestClient(app) as client:
        listed = client.get("/api/models")
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["total"] == len(DEFAULT_REGISTRY.all())
        assert sum(len(item["files"]) for item in payload["items"]) == sum(
            len(model.files) for model in DEFAULT_REGISTRY.all()
        )
        assert len(payload["registry_digest"]) == 64
        assert {item["installation_status"] for item in payload["items"]} == {"missing"}
        assert all(item["remote_code_allowed"] is False for item in payload["items"])

        health = client.get("/api/health").json()
        assert health["models"]["registered_models"] == len(DEFAULT_REGISTRY.all())
        assert health["models"]["ready_models"] == 0
        assert health["models"]["remote_code_allowed"] is False

        missing = client.get("/api/models/not_registered")
        assert missing.status_code == 404
        assert missing.json()["code"] == "model_not_found"
        missing_download = client.post(
            "/api/models/not_registered/download",
            json={"include_dependencies": False},
        )
        assert missing_download.status_code == 404

        registered = client.post(
            "/api/models/local",
            json={
                "base_model_id": "aesthetic_lse14_5k",
                "source_path": str(source),
                "display_name": "My 5K replacement",
            },
        )
        assert registered.status_code == 201
        custom = registered.json()
        assert custom["installation_status"] == "ready"
        assert custom["is_custom"] is True
        assert custom["base_model_id"] == "aesthetic_lse14_5k"
        assert custom["revision"] == before[2]
        assert custom["runtime_ready"] is False
        assert custom["blocking_dependencies"] == [
            "jtp3_hydra",
            "waifu_scorer_v3",
            "openai_clip_vit_l14",
        ]
        assert str(source) not in registered.text

        fetched = client.get(f"/api/models/{custom['id']}")
        assert fetched.status_code == 200
        verified = client.post(f"/api/models/{custom['id']}/verify")
        assert verified.status_code == 200
        assert verified.json()["operation"] is None
        assert verified.json()["model"]["installation_status"] == "ready"

        relisted = client.get("/api/models", params={"status": "ready"}).json()
        assert relisted["total"] == 1
        assert relisted["items"][0]["id"] == custom["id"]

        no_operation = client.post("/api/models/dinov2_large/cancel")
        assert no_operation.status_code == 409

    after = (
        source.stat().st_size,
        source.stat().st_mtime_ns,
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    assert after == before


def test_local_model_api_rejects_invalid_schema(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.safetensors"
    invalid.write_bytes(b"not safetensors")
    app = create_app(
        database_path=tmp_path / "api.db",
        enforce_runtime=False,
        models_root=tmp_path / "models",
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/models/local",
            json={
                "base_model_id": "aesthetic_lse14_5k",
                "source_path": str(invalid),
            },
        )
    assert response.status_code == 422
    assert response.json()["code"] == "ModelSchemaError"
