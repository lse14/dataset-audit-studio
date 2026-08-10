from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="feature.clip_l14",
    version="1.0.0",
    phase_order=30,
    config_schema="feature.clip_l14.config.v1",
    task_phase="model_scoring",
    consumes=(CapabilityRequirement("sample.image.v1"),),
    produces=(
        CapabilityDeclaration("embedding.clip_l14.aesthetic.v1"),
        CapabilityDeclaration("embedding.clip_l14.ufd.v1"),
    ),
    model_ids=("openai_clip_vit_l14",),
    execution="gpu_process",
    default_enabled=False,
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
