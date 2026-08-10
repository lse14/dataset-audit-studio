from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="metrics.technical",
    version="1.0.0",
    phase_order=20,
    config_schema="metrics.technical.config.v1",
    task_phase="cpu_metrics",
    consumes=(
        CapabilityRequirement("sample.manifest.v1"),
        CapabilityRequirement("sample.image.v1"),
    ),
    produces=(
        CapabilityDeclaration("evidence.technical.v1"),
        CapabilityDeclaration("resolution.fit.v1"),
    ),
    execution="cpu_process",
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
