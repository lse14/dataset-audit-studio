from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="evidence.watermark",
    version="1.0.0",
    phase_order=43,
    config_schema="evidence.watermark.config.v1",
    task_phase="model_scoring",
    consumes=(CapabilityRequirement("sample.image.v1"),),
    produces=(CapabilityDeclaration("evidence.watermark.v1"),),
    model_ids=("watermark_siglip2",),
    execution="gpu_process",
    default_enabled=True,
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
