from __future__ import annotations

from pathlib import Path, PurePosixPath

from dataset_audit_studio.components.latent_resolver.common import (
    require_regular_file,
    sha256_file,
    source_is_unchanged,
)
from dataset_audit_studio.components.latent_resolver.config import SingleLatentRule
from dataset_audit_studio.components.latent_resolver.contracts import (
    LatentCopy,
    LatentPlan,
    LatentRecord,
    LatentSample,
)


def plan_single_file_latents(
    source_root: Path,
    samples: tuple[LatentSample, ...],
    rules: tuple[SingleLatentRule, ...],
    *,
    verified_sample_ids: frozenset[str] = frozenset(),
) -> LatentPlan:
    if not rules:
        return LatentPlan(copies=(), catalogs=(), records=())
    source_root = source_root.resolve(strict=True)
    copies: dict[str, LatentCopy] = {}
    records: list[LatentRecord] = []
    for sample in samples:
        if (
            sample.sample_id not in verified_sample_ids
            and not source_is_unchanged(sample)
        ):
            continue
        relative = PurePosixPath(sample.relative_path)
        for rule in rules:
            filename = rule.pattern.format(stem=relative.stem, name=relative.name)
            candidate = sample.source_path.parent / filename
            if not candidate.exists():
                continue
            resolved = require_regular_file(candidate, source_root)
            destination = (relative.parent / filename).as_posix()
            digest = sha256_file(resolved)
            planned = LatentCopy(
                source_path=resolved,
                destination_relative=destination,
                sha256=digest,
                size_bytes=resolved.stat().st_size,
                kind=rule.cache_kind,
            )
            existing = copies.get(destination.casefold())
            if existing is not None and existing.source_path != resolved:
                raise RuntimeError(f"Single latent rules collide at {destination}")
            copies[destination.casefold()] = planned
            source_relative = resolved.relative_to(source_root).as_posix()
            records.append(
                LatentRecord(
                    sample_id=sample.sample_id,
                    cache_kind=rule.cache_kind,
                    source_path=source_relative,
                    namespace=None,
                    shard_path=None,
                    entry_key=None,
                    image_sha256=sample.source_sha256,
                    compatibility={"rule": rule.name},
                    metadata={
                        "latent_sha256": digest,
                        "latent_size": resolved.stat().st_size,
                    },
                )
            )
    return LatentPlan(
        copies=tuple(sorted(copies.values(), key=lambda item: item.destination_relative)),
        catalogs=(),
        records=tuple(records),
    )
