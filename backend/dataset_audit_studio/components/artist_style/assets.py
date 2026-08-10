from __future__ import annotations

import hashlib
import json

from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.core.model_assets import RuntimeAssets

DINO_MODEL_ID = "dinov2_large"
LSNET_MODEL_ID = "lsnet_kaloscope_v2"
VGG_MODEL_ID = "vgg19_imagenet1k_v1"
STYLE_MODEL_ID = "artist_style_lsnet_v2"
STYLE_PREPROCESSING_VERSION = "lsnet-kaloscope-v2-448-v1+vgg19-pooled-gram-16-v2+dinov2-pad224-v1"


def requested_style_model_ids(config: StyleConfig) -> tuple[str, ...]:
    if not config.enabled:
        return ()
    model_ids: list[str] = []
    if config.lsnet_weight > 0.0:
        model_ids.append(LSNET_MODEL_ID)
    if config.gram_weight > 0.0:
        model_ids.append(VGG_MODEL_ID)
    if config.dino_weight > 0.0:
        model_ids.append(DINO_MODEL_ID)
    return tuple(model_ids)


def style_identity(config: StyleConfig, assets: RuntimeAssets) -> tuple[str, str]:
    model_digest = hashlib.sha256()
    for model_id in requested_style_model_ids(config):
        model = assets.get(model_id)
        for file in sorted(model.files, key=lambda item: item.path):
            model_digest.update(f"{model_id}\0{file.path}\0{file.sha256}\n".encode())
    config_payload = json.dumps(
        config.analysis_payload(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return model_digest.hexdigest(), hashlib.sha256(config_payload).hexdigest()
