from dataset_audit_studio.components.artist_style.assets import requested_style_model_ids
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.core.component_config import ComponentConfigNormalizer
from dataset_audit_studio.core.component_contracts import (
    CapabilityDeclaration,
    CapabilityRequirement,
    ComponentDefinition,
    ComponentManifest,
    NormalizedComponentConfig,
)

MANIFEST = ComponentManifest(
    id="style.artist",
    version="2.0.0",
    phase_order=50,
    config_schema="style.artist.config.v2",
    task_phase="style_analysis",
    consumes=(
        CapabilityRequirement("sample.manifest.v1"),
        CapabilityRequirement("sample.image.v1"),
    ),
    produces=(CapabilityDeclaration("score.artist_style.v2"),),
    model_ids=("lsnet_kaloscope_v2", "vgg19_imagenet1k_v1", "dinov2_large"),
    execution="gpu_process",
)


def _resolve_model_ids(config: NormalizedComponentConfig) -> tuple[str, ...]:
    if not config.enabled:
        return ()
    return requested_style_model_ids(StyleConfig.model_validate(config.config))


DEFINITION = ComponentDefinition(
    MANIFEST,
    ComponentConfigNormalizer(MANIFEST.id),
    resolve_models=_resolve_model_ids,
)
