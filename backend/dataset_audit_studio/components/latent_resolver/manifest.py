from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="latent.resolve",
    version="1.0.0",
    phase_order=110,
    config_schema="latent.resolve.config.v1",
    task_phase="exporting",
    consumes=(
        CapabilityRequirement("sample.manifest.v1"),
        CapabilityRequirement("sample.image.v1"),
    ),
    produces=(CapabilityDeclaration("latent.reference.v1"),),
    execution="cpu_process",
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
