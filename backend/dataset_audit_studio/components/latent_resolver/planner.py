from __future__ import annotations

from pathlib import Path

from dataset_audit_studio.components.latent_resolver.config import LatentConfig
from dataset_audit_studio.components.latent_resolver.contracts import (
    LatentPlan,
    LatentSample,
)
from dataset_audit_studio.components.latent_resolver.mikazuki import (
    plan_mikazuki_namespace,
)
from dataset_audit_studio.components.latent_resolver.single_file import (
    plan_single_file_latents,
)


def plan_latent_reuse(
    source_root: Path,
    samples: tuple[LatentSample, ...],
    config: LatentConfig,
    *,
    verified_sample_ids: frozenset[str] = frozenset(),
) -> LatentPlan:
    plans: list[LatentPlan] = []
    if config.mikazuki_enabled:
        plans.extend(
            plan_mikazuki_namespace(
                source_root,
                samples,
                namespace=namespace,
                verified_sample_ids=verified_sample_ids,
            )
            for namespace in config.mikazuki_namespaces
        )
    plans.append(
        plan_single_file_latents(
            source_root,
            samples,
            config.single_file_rules,
            verified_sample_ids=verified_sample_ids,
        )
    )
    copies = [copy for plan in plans for copy in plan.copies]
    destinations: dict[str, Path] = {}
    for copy in copies:
        key = copy.destination_relative.casefold()
        previous = destinations.get(key)
        if previous is not None and previous != copy.source_path:
            raise RuntimeError(f"Latent adapters collide at {copy.destination_relative}")
        destinations[key] = copy.source_path
    return LatentPlan(
        copies=tuple(copies),
        catalogs=tuple(catalog for plan in plans for catalog in plan.catalogs),
        records=tuple(record for plan in plans for record in plan.records),
    )
