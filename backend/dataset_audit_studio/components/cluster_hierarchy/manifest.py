from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="cluster.hierarchy",
    version="1.0.0",
    phase_order=70,
    config_schema="cluster.hierarchy.config.v1",
    task_phase="semantic_clustering",
    consumes=(
        CapabilityRequirement("sample.manifest.v1"),
        CapabilityRequirement("embedding.semantic.v1"),
    ),
    produces=(CapabilityDeclaration("cluster.tree.v1"),),
    execution="cpu_process",
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
