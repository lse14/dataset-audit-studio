from __future__ import annotations

from typing import Any

from dataset_audit_studio.scoring.config import ScoringConfig
from dataset_audit_studio.scoring.types import ComponentIdentity, EvidenceRecord


def evidence_for_result(
    component: str,
    result: dict[str, Any],
    config: ScoringConfig,
    identity: ComponentIdentity,
) -> tuple[EvidenceRecord, ...]:
    if component == "aesthetic":
        return _aesthetic_evidence(result, config, identity)
    if component == "ai":
        return _ai_evidence(result, config, identity)
    if component == "ocr":
        return _ocr_evidence(result, config, identity)
    if component == "watermark":
        return _watermark_evidence(result, config, identity)
    raise ValueError(f"Unknown scoring component: {component}")


def _record(
    identity: ComponentIdentity,
    *,
    code: str,
    value: Any,
    threshold: Any | None = None,
    severity: str = "info",
    review_only: bool = False,
    bbox: list[float] | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        code=code,
        source=identity.evidence_source,
        value=value,
        threshold=threshold,
        severity=severity,
        review_only=review_only,
        bbox=bbox,
        algorithm_version=identity.preprocessing_version,
        metadata={
            "model_id": identity.model_id,
            "model_sha256": identity.model_sha256,
            "config_hash": identity.config_hash,
            **(metadata or {}),
        },
    )


def _aesthetic_evidence(
    result: dict[str, Any], config: ScoringConfig, identity: ComponentIdentity
) -> tuple[EvidenceRecord, ...]:
    records = [
        _record(
            identity,
            code="aesthetic_score",
            value=float(result["aesthetic"]),
        )
    ]
    probability = result.get("in_domain_prob")
    if probability is not None:
        probability = float(probability)
        threshold = config.aesthetic.in_domain_threshold
        passed = probability >= threshold
        records.append(
            _record(
                identity,
                code="in_domain_probability",
                value=probability,
                threshold=threshold,
                severity="info" if passed else "high",
                metadata={"in_domain_pass": passed},
            )
        )
    return tuple(records)


def _ai_evidence(
    result: dict[str, Any], config: ScoringConfig, identity: ComponentIdentity
) -> tuple[EvidenceRecord, ...]:
    probability = float(result["probability"])
    threshold = config.ai.candidate_threshold
    candidate = probability >= threshold
    return (
        _record(
            identity,
            code="ai_generated_probability",
            value=probability,
            threshold=threshold,
            severity="high" if candidate else "info",
            review_only=True,
            metadata={
                "candidate": candidate,
                "reference_threshold": config.ai.reference_threshold,
            },
        ),
    )


def _watermark_evidence(
    result: dict[str, Any], config: ScoringConfig, identity: ComponentIdentity
) -> tuple[EvidenceRecord, ...]:
    probability = float(result["watermark_probability"])
    threshold = config.watermark.review_threshold
    candidate = probability >= threshold
    return (
        _record(
            identity,
            code="watermark_probability",
            value=probability,
            threshold=threshold,
            severity="medium" if candidate else "info",
            review_only=True,
            metadata={
                "candidate": candidate,
                "probabilities": dict(result.get("probabilities", {})),
            },
        ),
    )


def _ocr_evidence(
    result: dict[str, Any], config: ScoringConfig, identity: ComponentIdentity
) -> tuple[EvidenceRecord, ...]:
    regions = list(result.get("regions", []))
    area_ratio = float(result.get("text_area_ratio", 0.0))
    threshold = config.ocr.text_density_threshold
    records: list[EvidenceRecord] = [
        _record(identity, code="ocr_text_box_count", value=len(regions)),
        _record(
            identity,
            code="ocr_text_area_ratio",
            value=area_ratio,
            threshold=threshold,
            severity="medium" if area_ratio >= threshold else "info",
        ),
    ]
    for region in regions:
        box = [float(value) for point in region.get("box", []) for value in point]
        records.append(
            _record(
                identity,
                code="ocr_text_region",
                value={
                    "text": str(region.get("text", "")),
                    "detection_score": float(region.get("detection_score", 0.0)),
                    "recognition_score": float(region.get("recognition_score", 0.0)),
                },
                severity="info",
                review_only=True,
                bbox=box or None,
            )
        )
    return tuple(records)
