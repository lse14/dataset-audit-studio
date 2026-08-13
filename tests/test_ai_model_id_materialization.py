from __future__ import annotations

import copy

from dataset_audit_studio.app.component_task_config import ComponentTaskConfigMaterializer
from dataset_audit_studio.app.profile_materialization import materialize_profile
from dataset_audit_studio.components.ai_detection.config import (
    COMMUNITY_FORENSICS_MODEL_ID,
    UFD_MODEL_ID,
)


def _general_components() -> dict:
    return copy.deepcopy(materialize_profile("general")["components"])


def test_detect_ai_missing_model_id_materializes_as_ufd() -> None:
    components = _general_components()
    components["detect.ai"]["enabled"] = True
    del components["detect.ai"]["config"]["model_id"]

    materialized = ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )

    assert materialized["scoring"]["ai"]["model_id"] == UFD_MODEL_ID
    assert materialized["components"]["detect.ai"]["config"]["model_id"] == UFD_MODEL_ID


def test_detect_ai_explicit_community_forensics_stays_cf() -> None:
    components = _general_components()
    components["detect.ai"]["enabled"] = True
    components["detect.ai"]["config"]["model_id"] = COMMUNITY_FORENSICS_MODEL_ID

    materialized = ComponentTaskConfigMaterializer().materialize(
        components,
        profile="general",
        require_profile=True,
    )

    assert materialized["scoring"]["ai"]["model_id"] == COMMUNITY_FORENSICS_MODEL_ID
    assert (
        materialized["components"]["detect.ai"]["config"]["model_id"]
        == COMMUNITY_FORENSICS_MODEL_ID
    )


def test_fresh_profile_detect_ai_default_stays_community_forensics() -> None:
    profile = materialize_profile("general")
    detect_ai = profile["components"]["detect.ai"]

    assert detect_ai["config"]["model_id"] == COMMUNITY_FORENSICS_MODEL_ID

    materialized = ComponentTaskConfigMaterializer().materialize(
        profile["components"],
        profile="general",
        require_profile=True,
    )

    assert materialized["scoring"]["ai"]["model_id"] == COMMUNITY_FORENSICS_MODEL_ID
    assert (
        materialized["components"]["detect.ai"]["config"]["model_id"]
        == COMMUNITY_FORENSICS_MODEL_ID
    )
