from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="media.scan",
    version="1.0.0",
    phase_order=10,
    config_schema="media.scan.config.v1",
    task_phase="scanning",
    consumes=(CapabilityRequirement("source.dataset.v1"),),
    produces=(
        CapabilityDeclaration("sample.manifest.v1"),
        CapabilityDeclaration("sample.image.v1"),
    ),
    execution="cpu_process",
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
