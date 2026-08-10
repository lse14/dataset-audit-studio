from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="export.dataset",
    version="1.0.0",
    phase_order=120,
    config_schema="export.dataset.config.v1",
    task_phase="exporting",
    consumes=(
        CapabilityRequirement("latent.reference.v1", optional=True),
    ),
    produces=(CapabilityDeclaration("export.dataset.v1"),),
    execution="cpu_process",
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
