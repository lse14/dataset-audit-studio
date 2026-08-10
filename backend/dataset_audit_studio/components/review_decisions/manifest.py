from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
)

MANIFEST = ComponentManifest(
    id="review.decisions",
    version="1.0.0",
    phase_order=80,
    config_schema="review.decisions.config.v1",
    task_phase="review_gates",
    consumes=(
        CapabilityRequirement("evidence.ai_candidate.v1", optional=True),
        CapabilityRequirement("score.artist_style.v1", optional=True),
        CapabilityRequirement("evidence.watermark.v1", optional=True),
        CapabilityRequirement("analysis.sae.v1", optional=True),
    ),
    produces=(CapabilityDeclaration("review.decision.v1"),),
    execution="cpu_inline",
)
DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
)
