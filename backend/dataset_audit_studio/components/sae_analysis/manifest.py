from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="analysis.sae",
    version="1.0.0",
    phase_order=69,
    config_schema="analysis.sae.config.v1",
    task_phase="semantic_clustering",
    consumes=(CapabilityRequirement("embedding.semantic.v1"),),
    produces=(CapabilityDeclaration("analysis.sae.v1"),),
    execution="gpu_process",
    default_enabled=False,
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
