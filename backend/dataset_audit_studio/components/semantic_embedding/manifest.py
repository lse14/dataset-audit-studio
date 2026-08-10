from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="embedding.semantic",
    version="1.0.0",
    phase_order=60,
    config_schema="embedding.semantic.config.v1",
    task_phase="semantic_clustering",
    consumes=(CapabilityRequirement("sample.image.v1"),),
    produces=(CapabilityDeclaration("embedding.semantic.v1"),),
    model_ids=("siglip2_so400m_naflex",),
    execution="gpu_process",
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
