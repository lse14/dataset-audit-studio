from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataset_audit_studio.components.artist_style.assets import (
    STYLE_MODEL_ID,
    STYLE_PREPROCESSING_VERSION,
)
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.database.models import (
    ComponentRun,
    Evidence,
    ResolutionAssessment,
    ReviewDecision,
    Sample,
    Task,
    TaskConfig,
)
from dataset_audit_studio.database.review_overlay import active_human_overlay_by_sample
from dataset_audit_studio.export_runs.errors import ExportRunError
from dataset_audit_studio.scoring.assets import EVIDENCE_SOURCES, PREPROCESSING_VERSIONS
from dataset_audit_studio.scoring.config import ScoringConfig

_DUPLICATE_SOURCE = "duplicate_evidence.v1"
_DUPLICATE_ALGORITHM = "duplicate_evidence.v1"
_DUPLICATE_CODES = ("duplicate_exact", "duplicate_visual")
_INVALID_REASONS = (
    "missing",
    "non_finite",
    "out_of_range",
    "provenance_mismatch",
    "ambiguous",
)
ELIGIBILITY_REASONS = (
    "included",
    "resolution_below_minimum",
    "manual_exclude",
    "domain_below_minimum",
    *(f"domain_{reason}" for reason in _INVALID_REASONS),
    "aesthetic_below_minimum",
    *(f"aesthetic_{reason}" for reason in _INVALID_REASONS),
    "style_outlier",
    *(f"style_{reason}" for reason in _INVALID_REASONS),
    "duplicate_non_representative",
    "folder_below_minimum",
)


@dataclass(frozen=True)
class EligibilityOutcome:
    reason: str | None
    decision_id: str | None
    decision: str | None
    representative_group: str | None = None


@dataclass(frozen=True)
class EligibilityResult:
    outcomes: dict[str, EligibilityOutcome]
    exclusion_counts: dict[str, int]
    eligibility_digest: str
    evidence_provenance: dict[str, list[dict[str, Any]]]
    duplicate_groups: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


class _UnionFind:
    def __init__(self, values: set[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


class EligibilityResolver:
    """Resolve copy-export eligibility without mutating task-owned audit data."""

    def resolve(
        self,
        session: Session,
        *,
        task: Task,
        config: TaskConfig,
        rows: list[Sample],
        settings: dict[str, Any],
    ) -> EligibilityResult:
        outcomes: dict[str, EligibilityOutcome] = {}
        eligible: list[Sample] = []
        minimum = settings["minimum_resolution"]
        for row in rows:
            if self._area(row) < minimum * minimum:
                outcomes[row.id] = EligibilityOutcome("resolution_below_minimum", None, None)
            else:
                eligible.append(row)

        overlays = active_human_overlay_by_sample(
            session, task_id=task.id, sample_ids=tuple(row.id for row in eligible)
        )
        active: dict[str, tuple[str | None, str | None]] = {}
        for sample_id, decisions in overlays.items():
            latest = decisions[0]
            active[sample_id] = (latest.id, latest.decision)

        evidence_by_code = self._evidence_by_code(
            session,
            task_id=task.id,
            sample_ids=tuple(row.id for row in eligible),
            codes=self._required_codes(settings),
        )
        domain_identity = self._scoring_identity(config.config_json)
        aesthetic_identity = domain_identity
        style_config = StyleConfig.from_task_config(config.config_json)
        style_scopes = (
            self._style_scope_identities(session, task_id=task.id, config=style_config)
            if settings["style_outlier_mode"] != "off"
            else {}
        )
        for row in eligible:
            decision_id, decision = active.get(row.id, (None, None))
            if decision == "approved_exclude":
                outcomes[row.id] = EligibilityOutcome("manual_exclude", decision_id, decision)
                continue
            if decision == "approved_keep":
                outcomes[row.id] = EligibilityOutcome(None, decision_id, decision)
                continue
            if settings["domain_minimum"] is not None:
                value, invalid = self._numeric_evidence(
                    evidence_by_code["in_domain_probability"].get(row.id, ()),
                    identity=domain_identity,
                    lower=0.0,
                    upper=1.0,
                )
                if invalid is not None:
                    outcomes[row.id] = EligibilityOutcome(
                        f"domain_{invalid}", decision_id, decision
                    )
                    continue
                if value is None or value < settings["domain_minimum"]:
                    outcomes[row.id] = EligibilityOutcome(
                        "domain_below_minimum", decision_id, decision
                    )
                    continue
            if settings["aesthetic_minimum"] is not None:
                value, invalid = self._numeric_evidence(
                    evidence_by_code["aesthetic_score"].get(row.id, ()),
                    identity=aesthetic_identity,
                    lower=1.0,
                    upper=5.0,
                )
                if invalid is not None:
                    outcomes[row.id] = EligibilityOutcome(
                        f"aesthetic_{invalid}", decision_id, decision
                    )
                    continue
                if value is None or value < settings["aesthetic_minimum"]:
                    outcomes[row.id] = EligibilityOutcome(
                        "aesthetic_below_minimum", decision_id, decision
                    )
                    continue
            style_mode = settings["style_outlier_mode"]
            if style_mode != "off":
                classification, invalid = self._style_classification(
                    evidence_by_code["artist_style_score"].get(row.id, ()),
                    row=row,
                    config=style_config,
                    expected_scope=style_scopes.get(row.id),
                )
                if invalid is not None:
                    outcomes[row.id] = EligibilityOutcome(
                        f"style_{invalid}", decision_id, decision
                    )
                    continue
                if classification == "strong_outlier" or (
                    style_mode == "all" and classification == "outlier"
                ):
                    outcomes[row.id] = EligibilityOutcome("style_outlier", decision_id, decision)
                    continue
            outcomes[row.id] = EligibilityOutcome(None, decision_id, decision)

        evidence_provenance = self._evidence_identity(evidence_by_code)
        duplicate_groups: tuple[dict[str, Any], ...] = ()
        warnings: tuple[str, ...] = ()
        if settings["exclude_exact_visual_duplicates"]:
            duplicate_groups, warnings, duplicate_provenance = self._apply_duplicate_filter(
                session,
                task=task,
                config=config,
                rows=rows,
                outcomes=outcomes,
            )
            evidence_provenance["duplicate"] = duplicate_provenance
        counts = {reason: 0 for reason in ELIGIBILITY_REASONS}
        for outcome in outcomes.values():
            counts[outcome.reason or "included"] += 1
        payload = {
            "schema": "export.run.eligibility.v1",
            "task_id": task.id,
            "config_hash": config.config_hash,
            "settings": self._safe_json(settings),
            "outcomes": {
                sample_id: {
                    "reason": outcome.reason,
                    "decision_id": outcome.decision_id,
                    "decision": outcome.decision,
                    "representative_group": outcome.representative_group,
                }
                for sample_id, outcome in sorted(outcomes.items())
            },
            "evidence": evidence_provenance,
            "duplicate_groups": duplicate_groups,
        }
        serialized = json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
        return EligibilityResult(
            outcomes=outcomes,
            exclusion_counts=counts,
            eligibility_digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            evidence_provenance=evidence_provenance,
            duplicate_groups=duplicate_groups,
            warnings=warnings,
        )

    @staticmethod
    def _area(row: Sample) -> int:
        width = row.display_width or row.encoded_width
        height = row.display_height or row.encoded_height
        if (
            isinstance(width, bool)
            or not isinstance(width, int)
            or isinstance(height, bool)
            or not isinstance(height, int)
            or width < 1
            or height < 1
        ):
            return 0
        return width * height

    @staticmethod
    def _required_codes(settings: dict[str, Any]) -> tuple[str, ...]:
        codes: list[str] = []
        if settings["domain_minimum"] is not None:
            codes.append("in_domain_probability")
        if settings["aesthetic_minimum"] is not None:
            codes.append("aesthetic_score")
        if settings["style_outlier_mode"] != "off":
            codes.append("artist_style_score")
        return tuple(codes)

    @staticmethod
    def _evidence_by_code(
        session: Session,
        *,
        task_id: str,
        sample_ids: tuple[str, ...],
        codes: tuple[str, ...],
    ) -> dict[str, dict[str, tuple[Evidence, ...]]]:
        result = {code: {} for code in codes}
        if not sample_ids or not codes:
            return result
        rows = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task_id,
                Evidence.sample_id.in_(sample_ids),
                Evidence.code.in_(codes),
            )
            .order_by(Evidence.code, Evidence.sample_id, Evidence.id)
        ).all()
        grouped: dict[str, dict[str, list[Evidence]]] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            grouped[row.code][row.sample_id].append(row)
        for code, by_sample in grouped.items():
            result[code] = {sample_id: tuple(items) for sample_id, items in by_sample.items()}
        return result

    @staticmethod
    def _scoring_identity(config: dict[str, Any]) -> dict[str, str]:
        scoring = ScoringConfig.from_task_config(config)
        return {
            "source": EVIDENCE_SOURCES["aesthetic"],
            "model_id": scoring.aesthetic.model_id,
            "config_hash": scoring.inference_config_hash("aesthetic"),
            "algorithm_version": PREPROCESSING_VERSIONS["aesthetic"],
        }

    @staticmethod
    def _numeric_evidence(
        rows: tuple[Evidence, ...],
        *,
        identity: dict[str, str],
        lower: float,
        upper: float,
    ) -> tuple[float | None, str | None]:
        if not rows:
            return None, "missing"
        matching = [row for row in rows if EligibilityResolver._matches_identity(row, identity)]
        if not matching or len(matching) != len(rows):
            return None, "provenance_mismatch"
        values: list[float] = []
        for row in matching:
            value = row.value_json
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                value = row.value_number
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None, "missing"
            normalized = float(value)
            if not math.isfinite(normalized):
                return None, "non_finite"
            if not lower <= normalized <= upper:
                return None, "out_of_range"
            values.append(normalized)
        if len(set(values)) != 1:
            return None, "ambiguous"
        return values[0], None

    @staticmethod
    def _matches_identity(row: Evidence, identity: dict[str, str]) -> bool:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        return (
            row.source == identity["source"]
            and row.algorithm_version == identity["algorithm_version"]
            and metadata.get("model_id") == identity["model_id"]
            and metadata.get("config_hash") == identity["config_hash"]
        )

    @staticmethod
    def _style_classification(
        rows: tuple[Evidence, ...],
        *,
        row: Sample,
        config: StyleConfig,
        expected_scope: tuple[str, int] | None,
    ) -> tuple[str | None, str | None]:
        if not rows:
            return None, "missing"
        if expected_scope is None:
            return None, "provenance_mismatch"
        expected_hash, expected_size = expected_scope
        classifications: list[str] = []
        for evidence in rows:
            metadata = evidence.metadata_json if isinstance(evidence.metadata_json, dict) else {}
            scope_hash = metadata.get("config_hash")
            if (
                evidence.source != "artist_style_v1"
                or evidence.algorithm_version != STYLE_PREPROCESSING_VERSION
                or metadata.get("model_id") != STYLE_MODEL_ID
                or scope_hash != expected_hash
                or metadata.get("scope_id") != row.artist_scope
                or metadata.get("scope_size") != expected_size
                or evidence.threshold_number != config.minimum_style_score
            ):
                return None, "provenance_mismatch"
            strong = metadata.get("strong_outlier")
            review = metadata.get("review_required")
            if not isinstance(strong, bool) or not isinstance(review, bool):
                return None, "provenance_mismatch"
            if evidence.severity == "high" and strong and review:
                classifications.append("strong_outlier")
            elif evidence.severity == "medium" and not strong and review:
                classifications.append("outlier")
            elif evidence.severity == "info" and not strong and not review:
                classifications.append("normal")
            else:
                return None, "provenance_mismatch"
        if len(set(classifications)) != 1:
            return None, "ambiguous"
        return classifications[0], None

    @staticmethod
    def _style_scope_identities(
        session: Session, *, task_id: str, config: StyleConfig
    ) -> dict[str, tuple[str, int]]:
        has_assessments = session.scalar(
            select(ResolutionAssessment.id)
            .where(ResolutionAssessment.task_id == task_id)
            .limit(1)
        )
        samples_query = (
            select(Sample)
            .where(
                Sample.task_id == task_id,
                Sample.scan_state == "valid",
                Sample.pixel_sha256.is_not(None),
            )
            .order_by(Sample.relative_path, Sample.id)
        )
        if has_assessments is not None:
            eligible_samples = select(ResolutionAssessment.sample_id).where(
                ResolutionAssessment.task_id == task_id,
                ResolutionAssessment.eligible.is_(True),
            )
            samples_query = samples_query.where(Sample.id.in_(eligible_samples))
        excluded_ai = set(
            session.scalars(
                select(ReviewDecision.sample_id).where(
                    ReviewDecision.task_id == task_id,
                    ReviewDecision.category == "ai_generated",
                    ReviewDecision.decision == "approved_exclude",
                    ReviewDecision.is_active.is_(True),
                    ReviewDecision.sample_id.is_not(None),
                )
            ).all()
        )
        outside_domain = set(
            session.scalars(
                select(Evidence.sample_id).where(
                    Evidence.task_id == task_id,
                    Evidence.code == "in_domain_probability",
                    Evidence.value_number.is_not(None),
                    Evidence.threshold_number.is_not(None),
                    Evidence.value_number < Evidence.threshold_number,
                )
            ).all()
        )
        scopes: dict[str, list[Sample]] = defaultdict(list)
        for sample in session.scalars(samples_query).all():
            if sample.id not in excluded_ai and sample.id not in outside_domain:
                scopes[sample.artist_scope].append(sample)
        identities: dict[str, tuple[str, int]] = {}
        for scope_id, members in scopes.items():
            payload = {
                "scope_id": scope_id,
                "samples": [
                    [sample.id, sample.pixel_sha256 or ""]
                    for sample in sorted(
                        members,
                        key=lambda sample: (sample.relative_path, sample.id),
                    )
                ],
                "analysis": config.analysis_payload(),
            }
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            scope_hash = hashlib.sha256(serialized.encode()).hexdigest()
            for sample in members:
                identities[sample.id] = (scope_hash, len(members))
        return identities

    def _apply_duplicate_filter(
        self,
        session: Session,
        *,
        task: Task,
        config: TaskConfig,
        rows: list[Sample],
        outcomes: dict[str, EligibilityOutcome],
    ) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...], list[dict[str, Any]]]:
        completed = session.scalar(
            select(ComponentRun.id).where(
                ComponentRun.task_id == task.id,
                ComponentRun.config_hash == config.config_hash,
                ComponentRun.component_id == "metrics.technical",
                ComponentRun.status == "completed",
            )
        )
        if completed is None:
            raise ExportRunError(
                "export_duplicate_analysis_incomplete",
                "Duplicate filtering requires completed metrics.technical evidence",
            )
        sample_ids = {row.id for row in rows}
        evidence_rows = session.scalars(
            select(Evidence)
            .where(
                Evidence.task_id == task.id,
                Evidence.code.in_(_DUPLICATE_CODES),
            )
            .order_by(Evidence.code, Evidence.sample_id, Evidence.id)
        ).all()
        groups: dict[tuple[str, str], set[str]] = defaultdict(set)
        for evidence in evidence_rows:
            metadata = evidence.metadata_json if isinstance(evidence.metadata_json, dict) else {}
            group_key = metadata.get("group_key")
            provenance = metadata.get("provenance")
            if (
                evidence.source != _DUPLICATE_SOURCE
                or evidence.algorithm_version != _DUPLICATE_ALGORITHM
                or not isinstance(group_key, str)
                or not group_key.strip()
                or metadata.get("config_hash") != config.config_hash
                or not isinstance(provenance, dict)
                or provenance.get("component_id") != "metrics.technical"
                or provenance.get("algorithm_version") != _DUPLICATE_ALGORITHM
                or evidence.sample_id not in sample_ids
            ):
                raise ExportRunError(
                    "export_duplicate_evidence_invalid",
                    "Duplicate evidence provenance is invalid",
                )
            groups[(evidence.code, group_key)].add(evidence.sample_id)
        for (code, group_key), members in groups.items():
            declared = [
                row.metadata_json.get("group_size")
                for row in evidence_rows
                if row.code == code
                and isinstance(row.metadata_json, dict)
                and row.metadata_json.get("group_key") == group_key
            ]
            if len(members) < 2 or any(value != len(members) for value in declared):
                raise ExportRunError(
                    "export_duplicate_evidence_invalid",
                    "Duplicate evidence group is malformed",
                )
        duplicate_provenance = [
            {
                "evidence_id": evidence.id,
                "sample_id": evidence.sample_id,
                "code": evidence.code,
                "source": evidence.source,
                "algorithm_version": evidence.algorithm_version,
                "metadata": self._safe_json(evidence.metadata_json),
            }
            for evidence in evidence_rows
        ]
        if not groups:
            return (), (), duplicate_provenance
        union = _UnionFind(set().union(*groups.values()))
        for members in groups.values():
            ordered = sorted(members)
            for member in ordered[1:]:
                union.union(ordered[0], member)
        connected: dict[str, set[str]] = defaultdict(set)
        keys_by_root: dict[str, set[str]] = defaultdict(set)
        for (code, group_key), members in groups.items():
            root = union.find(next(iter(members)))
            connected[root].update(members)
            keys_by_root[root].add(f"{code}:{group_key}")
        by_id = {row.id: row for row in rows}
        summaries: list[dict[str, Any]] = []
        for root in sorted(connected):
            members = tuple(sorted(connected[root]))
            member_outcomes = [outcomes[sample_id] for sample_id in members]
            if all(outcome.decision == "approved_exclude" for outcome in member_outcomes):
                raise ExportRunError(
                    "export_duplicate_group_fully_excluded",
                    "A duplicate group has no remaining human-approved member",
                )
            keeps = [
                sample_id
                for sample_id in members
                if outcomes[sample_id].decision == "approved_keep"
            ]
            representative: str | None = None
            if not keeps:
                candidates = [
                    sample_id for sample_id in members if outcomes[sample_id].reason is None
                ]
                if candidates:
                    representative = min(
                        candidates,
                        key=lambda sample_id: (
                            -self._area(by_id[sample_id]),
                            by_id[sample_id].relative_path.casefold(),
                            by_id[sample_id].relative_path,
                            sample_id,
                        ),
                    )
                    for sample_id in candidates:
                        if sample_id != representative:
                            outcome = outcomes[sample_id]
                            outcomes[sample_id] = EligibilityOutcome(
                                "duplicate_non_representative",
                                outcome.decision_id,
                                outcome.decision,
                                representative,
                            )
            summaries.append(
                {
                    "group_keys": sorted(keys_by_root[root]),
                    "member_count": len(members),
                    "manual_keep_count": len(keeps),
                    "representative_sample_id": representative,
                }
            )
        return tuple(summaries), (), duplicate_provenance

    @staticmethod
    def _evidence_identity(
        evidence_by_code: dict[str, dict[str, tuple[Evidence, ...]]]
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            code: [
                {
                    "sample_id": sample_id,
                    "evidence_ids": [row.id for row in rows],
                    "provenance": [
                        {
                            "source": row.source,
                            "algorithm_version": row.algorithm_version,
                            "metadata": EligibilityResolver._safe_json(row.metadata_json),
                        }
                        for row in rows
                    ],
                }
                for sample_id, rows in sorted(by_sample.items())
            ]
            for code, by_sample in sorted(evidence_by_code.items())
        }

    @staticmethod
    def _safe_json(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            return "non_finite"
        if isinstance(value, dict):
            return {str(key): EligibilityResolver._safe_json(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [EligibilityResolver._safe_json(item) for item in value]
        return value
