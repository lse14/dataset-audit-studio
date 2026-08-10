from dataset_audit_studio.components.ai_detection.config import (
    COMMUNITY_FORENSICS_MODEL_ID,
    UFD_MODEL_ID,
)
from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
    NormalizedComponentConfig,
)

MANIFEST = ComponentManifest(
    id="detect.ai",
    version="1.0.0",
    phase_order=41,
    config_schema="detect.ai.config.v1",
    task_phase="model_scoring",
    consumes=(CapabilityRequirement("embedding.clip_l14.ufd.v1"),),
    produces=(CapabilityDeclaration("evidence.ai_candidate.v1"),),
    model_ids=(COMMUNITY_FORENSICS_MODEL_ID,),
    execution="gpu_process",
    default_enabled=True,
)


def _models(config: NormalizedComponentConfig) -> tuple[str, ...]:
    # Existing persisted component configs predate model_id and must keep UFD.
    return (str(config.config.get("model_id", UFD_MODEL_ID)),)


DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
    resolve_models=_models,
)
