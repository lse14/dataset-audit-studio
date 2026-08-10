from __future__ import annotations

import hashlib
from pathlib import Path

from dataset_audit_studio.components.ai_detection.config import (
    COMMUNITY_FORENSICS_MODEL_ID,
    UFD_MODEL_ID,
)
from dataset_audit_studio.core.model_assets import (
    verify_runtime_asset_snapshot as _verify_runtime_asset_snapshot,
)
from dataset_audit_studio.model_adapters.service import ModelService
from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.types import (
    AssetFile,
    ComponentIdentity,
    ModelAsset,
    RuntimeAssets,
)

AI_MODEL_ID = UFD_MODEL_ID
CLIP_MODEL_ID = "openai_clip_vit_l14"
JTP3_MODEL_ID = "jtp3_hydra"
OCR_DET_MODEL_ID = "ppocrv5_server_det"
OCR_REC_MODEL_ID = "ppocrv5_server_rec"
WAIFU_MODEL_ID = "waifu_scorer_v3"
WATERMARK_MODEL_ID = "watermark_siglip2"
verify_runtime_asset_snapshot = _verify_runtime_asset_snapshot

PREPROCESSING_VERSIONS = {
    "aesthetic": "lse14-fusion-v1+jtp3-naflex-v1+openai-clip-v1+waifu-v3",
    "ai": "ufd-76a0e3e-center-crop-224-v1",
    "ocr": "ppocrv5-transformers-det-rec-v1",
    "watermark": "siglip-watermark-224-v1",
}

EVIDENCE_SOURCES = {
    "aesthetic": "aesthetic_lse14",
    "ai": "universal_fake_detector",
    "ocr": "ppocrv5",
    "watermark": "watermark_siglip2",
}

AI_PREPROCESSING_VERSIONS = {
    UFD_MODEL_ID: "ufd-76a0e3e-center-crop-224-v1",
    COMMUNITY_FORENSICS_MODEL_ID: "commfor-resize440-center-crop384-imagenet-v1",
}
AI_EVIDENCE_SOURCES = {
    UFD_MODEL_ID: "universal_fake_detector",
    COMMUNITY_FORENSICS_MODEL_ID: "community_forensics",
}


def ai_preprocessing_version(model_id: str) -> str:
    return AI_PREPROCESSING_VERSIONS[model_id]


def ai_evidence_source(model_id: str) -> str:
    return AI_EVIDENCE_SOURCES[model_id]


def requested_model_ids(config: ScoringConfig) -> tuple[str, ...]:
    ids: list[str] = []
    if "aesthetic" in config.enabled_components:
        ids.append(config.aesthetic.model_id)
    if "ai" in config.enabled_components:
        ids.append(config.ai.model_id)
    if "ocr" in config.enabled_components:
        ids.extend((OCR_DET_MODEL_ID, OCR_REC_MODEL_ID))
    if "watermark" in config.enabled_components:
        ids.append(WATERMARK_MODEL_ID)
    return tuple(dict.fromkeys(ids))


def resolve_runtime_assets(service: ModelService, model_ids: tuple[str, ...]) -> RuntimeAssets:
    collected: dict[str, ModelAsset] = {}

    def collect(model_id: str) -> None:
        if model_id in collected:
            return
        status = service.get_model(model_id)
        for dependency in status.dependencies:
            collect(dependency)
        root = service.require_ready(model_id)
        files: list[AssetFile] = []
        for expected in status.files:
            path = root.joinpath(*Path(expected.path).parts).resolve(strict=True)
            path.relative_to(root)
            stat = path.stat()
            files.append(
                AssetFile(
                    path=expected.path,
                    size=expected.size,
                    sha256=expected.sha256,
                    mtime_ns=stat.st_mtime_ns,
                )
            )
        collected[model_id] = ModelAsset(
            model_id=model_id,
            loader=status.loader,
            root=str(root),
            files=tuple(files),
            dependencies=status.dependencies,
            is_custom=status.is_custom,
            base_model_id=status.base_model_id,
        )

    for requested in model_ids:
        collect(requested)
    return RuntimeAssets(
        models_root=str(service.storage.models_root.resolve(strict=True)),
        models=tuple(collected[key] for key in sorted(collected)),
    )


def build_component_identities(
    config: ScoringConfig, assets: RuntimeAssets
) -> dict[str, ComponentIdentity]:
    dependencies = {
        "aesthetic": (config.aesthetic.model_id, JTP3_MODEL_ID, WAIFU_MODEL_ID, CLIP_MODEL_ID),
        "ai": (
            (config.ai.model_id, CLIP_MODEL_ID)
            if config.ai.model_id == UFD_MODEL_ID
            else (config.ai.model_id,)
        ),
        "ocr": (OCR_DET_MODEL_ID, OCR_REC_MODEL_ID),
        "watermark": (WATERMARK_MODEL_ID,),
    }
    model_ids = {
        "aesthetic": config.aesthetic.model_id,
        "ai": config.ai.model_id,
        "ocr": "ppocrv5_server_ocr",
        "watermark": WATERMARK_MODEL_ID,
    }
    identities: dict[str, ComponentIdentity] = {}
    for component in config.enabled_components:
        digest = hashlib.sha256()
        for model_id in dependencies[component]:
            model = assets.get(model_id)
            for file in sorted(model.files, key=lambda item: item.path):
                digest.update(f"{model_id}\0{file.path}\0{file.sha256}\n".encode())
        identities[component] = ComponentIdentity(
            component=component,
            model_id=model_ids[component],
            model_sha256=digest.hexdigest(),
            preprocessing_version=(
                ai_preprocessing_version(config.ai.model_id)
                if component == "ai"
                else PREPROCESSING_VERSIONS[component]
            ),
            config_hash=config.inference_config_hash(component),
            evidence_source=(
                ai_evidence_source(config.ai.model_id)
                if component == "ai"
                else EVIDENCE_SOURCES[component]
            ),
        )
    return identities
