from __future__ import annotations

import hashlib

from dataset_audit_studio.clustering.config import ClusteringConfig
from dataset_audit_studio.scoring.types import RuntimeAssets

SIGLIP_MODEL_ID = "siglip2_so400m_naflex"
SIGLIP_PREPROCESSING_VERSION = "siglip2-naflex-image-processor-v1"


def requested_clustering_model_ids(config: ClusteringConfig) -> tuple[str, ...]:
    return (SIGLIP_MODEL_ID,) if config.enabled else ()


def embedding_model_sha256(assets: RuntimeAssets) -> str:
    model = assets.get(SIGLIP_MODEL_ID)
    digest = hashlib.sha256()
    for file in sorted(model.files, key=lambda item: item.path):
        digest.update(f"{SIGLIP_MODEL_ID}\0{file.path}\0{file.sha256}\n".encode())
    return digest.hexdigest()
