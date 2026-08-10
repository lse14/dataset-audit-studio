from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="evidence.ocr",
    version="1.0.0",
    phase_order=42,
    config_schema="evidence.ocr.config.v1",
    task_phase="model_scoring",
    consumes=(CapabilityRequirement("sample.image.v1"),),
    produces=(CapabilityDeclaration("evidence.ocr.v1"),),
    model_ids=("ppocrv5_server_det", "ppocrv5_server_rec"),
    execution="gpu_process",
    default_enabled=True,
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
