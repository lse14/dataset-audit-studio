from __future__ import annotations

from dataset_audit_studio.clustering.config import ClusteringConfig, SelectionConfig
from dataset_audit_studio.jobs.errors import LegacyTaskConfigUnsupported


def select_resolution_stages(
    samples,
    fits,
    *,
    resolution,
    leaves,
    embeddings,
    embedding_rows,
    sae_risk_samples,
    clustering: ClusteringConfig,
    selection: SelectionConfig,
    style_enabled: bool,
    should_stop=None,
):
    raise LegacyTaskConfigUnsupported(
        "legacy_task_config_unsupported: nested stage selection is no longer supported"
    )


__all__ = ["select_resolution_stages"]
