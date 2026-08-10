from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
    NormalizedComponentConfig,
)

MANIFEST = ComponentManifest(
    id="score.aesthetic_domain",
    version="1.0.0",
    phase_order=40,
    config_schema="score.aesthetic_domain.config.v1",
    task_phase="model_scoring",
    consumes=(
        CapabilityRequirement("sample.image.v1"),
        CapabilityRequirement("embedding.clip_l14.aesthetic.v1"),
    ),
    produces=(
        CapabilityDeclaration("score.aesthetic.v1"),
        CapabilityDeclaration("score.domain.v1"),
    ),
    model_ids=("aesthetic_lse14_5k", "jtp3_hydra", "waifu_scorer_v3"),
    execution="gpu_process",
    default_enabled=True,
)


def _models(config: NormalizedComponentConfig) -> tuple[str, ...]:
    model_id = str(config.config.get("model_id", "aesthetic_lse14_5k"))
    return (model_id, "jtp3_hydra", "waifu_scorer_v3")


DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
    resolve_models=_models,
)
