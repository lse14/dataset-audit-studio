from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

CURATED_REASON_CODES = (
    "missing",
    "non_finite",
    "out_of_range",
    "provenance_mismatch",
    "ambiguous",
    "aesthetic_below_minimum",
    "approved_exclude",
    "approved_keep",
)


@dataclass(frozen=True)
class AestheticScoreRecord:
    value: object
    source: str | None = None
    model_id: str | None = None
    config_hash: str | None = None
    algorithm_version: str | None = None


@dataclass(frozen=True)
class CuratedMembership:
    sample_id: str
    broad: bool
    included: bool
    reason_code: str | None
    score: float | None = None


def _matches_identity(
    record: AestheticScoreRecord,
    identity: Mapping[str, str | None] | None,
) -> bool:
    if identity is None:
        return True
    return all(getattr(record, key) == value for key, value in identity.items())


def resolve_aesthetic_score(
    records: Sequence[AestheticScoreRecord],
    *,
    identity: Mapping[str, str | None] | None = None,
) -> tuple[float | None, str | None]:
    """Return one trusted score or an explicit review reason.

    Aesthetic evidence is deliberately conservative: a missing, non-finite,
    out-of-range, provenance-mismatched, or ambiguous value is never converted
    into a low score.
    """

    matching = [record for record in records if _matches_identity(record, identity)]
    if not matching:
        return None, "missing" if not records else "provenance_mismatch"
    if identity is not None and len(matching) != len(records):
        return None, "provenance_mismatch"
    numeric: list[float] = []
    for record in matching:
        value = record.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "missing"
        value = float(value)
        if not math.isfinite(value):
            return None, "non_finite"
        if not 1.0 <= value <= 5.0:
            return None, "out_of_range"
        numeric.append(value)
    if not numeric or len(set(numeric)) != 1:
        return None, "ambiguous"
    return numeric[0], None


def compute_curated_members(
    broad_sample_ids: Iterable[str],
    *,
    aesthetic_evidence: Mapping[str, Sequence[AestheticScoreRecord]] | None = None,
    aesthetic_minimum: float | None = None,
    aesthetic_identity: Mapping[str, str | None] | None = None,
    human_decisions: Mapping[str, str] | None = None,
) -> tuple[CuratedMembership, ...]:
    """Overlay curated decisions on an immutable broad sample set.

    The only automatic filter is the explicit aesthetic minimum. All other
    evidence remains a candidate until a human decision is supplied. Human
    keep/exclude decisions have the highest priority and never mutate broad.
    """

    if aesthetic_minimum is not None:
        if isinstance(aesthetic_minimum, bool) or not isinstance(
            aesthetic_minimum, (int, float)
        ):
            raise ValueError("aesthetic_minimum must be numeric")
        if (
            not math.isfinite(float(aesthetic_minimum))
            or not 1.0 <= float(aesthetic_minimum) <= 5.0
        ):
            raise ValueError("aesthetic_minimum must be finite and between 1 and 5")
    evidence_by_sample = aesthetic_evidence or {}
    decisions = human_decisions or {}
    results: list[CuratedMembership] = []
    for sample_id in broad_sample_ids:
        human = decisions.get(sample_id)
        if human == "approved_exclude":
            results.append(CuratedMembership(sample_id, True, False, "approved_exclude"))
            continue
        if human == "approved_keep":
            results.append(CuratedMembership(sample_id, True, True, "approved_keep"))
            continue
        if human not in (None, "approved_exclude", "approved_keep"):
            raise ValueError(f"Unsupported human decision: {human}")
        if aesthetic_minimum is None:
            results.append(CuratedMembership(sample_id, True, True, None))
            continue
        score, reason = resolve_aesthetic_score(
            tuple(evidence_by_sample.get(sample_id, ())),
            identity=aesthetic_identity,
        )
        if reason is not None:
            results.append(CuratedMembership(sample_id, True, False, reason, score))
        elif score is not None and score < float(aesthetic_minimum):
            results.append(
                CuratedMembership(sample_id, True, False, "aesthetic_below_minimum", score)
            )
        else:
            results.append(CuratedMembership(sample_id, True, True, None, score))
    return tuple(results)
