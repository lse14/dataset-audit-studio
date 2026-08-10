from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict

from dataset_audit_studio.components.aesthetic_domain import DEFINITION as AESTHETIC
from dataset_audit_studio.components.aesthetic_domain.config import (
    AestheticDomainConfig,
)
from dataset_audit_studio.components.ai_detection import DEFINITION as AI_DETECTION
from dataset_audit_studio.components.ai_detection.config import AIDetectionConfig
from dataset_audit_studio.components.artist_style import DEFINITION as ARTIST_STYLE
from dataset_audit_studio.components.artist_style.config import StyleConfig
from dataset_audit_studio.components.clip_features import DEFINITION as CLIP_FEATURES
from dataset_audit_studio.components.clip_features.config import ClipFeatureConfig
from dataset_audit_studio.components.cluster_hierarchy import DEFINITION as CLUSTER_HIERARCHY
from dataset_audit_studio.components.cluster_hierarchy.config import HierarchyConfig
from dataset_audit_studio.components.dataset_export import DEFINITION as DATASET_EXPORT
from dataset_audit_studio.components.dataset_export.config import DatasetExportConfig
from dataset_audit_studio.components.latent_resolver import DEFINITION as LATENT_RESOLVER
from dataset_audit_studio.components.latent_resolver.config import LatentConfig
from dataset_audit_studio.components.media_scan import DEFINITION as MEDIA_SCAN
from dataset_audit_studio.components.ocr_evidence import DEFINITION as OCR_EVIDENCE
from dataset_audit_studio.components.ocr_evidence.config import OCREvidenceConfig
from dataset_audit_studio.components.review_decisions import DEFINITION as REVIEW_DECISIONS
from dataset_audit_studio.components.sae_analysis import DEFINITION as SAE_ANALYSIS
from dataset_audit_studio.components.sae_analysis.config import SparseAutoencoderConfig
from dataset_audit_studio.components.semantic_embedding import (
    DEFINITION as SEMANTIC_EMBEDDING,
)
from dataset_audit_studio.components.semantic_embedding.config import (
    SemanticEmbeddingConfig,
)
from dataset_audit_studio.components.technical_metrics import (
    DEFINITION as TECHNICAL_METRICS,
)
from dataset_audit_studio.components.watermark_evidence import (
    DEFINITION as WATERMARK_EVIDENCE,
)
from dataset_audit_studio.components.watermark_evidence.config import (
    WatermarkEvidenceConfig,
)
from dataset_audit_studio.core.component_contracts import ComponentDefinition
from dataset_audit_studio.scanner.config import ScanConfig


class ComponentRegistrationError(ValueError):
    pass


class EmptyComponentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class ComponentUIContract:
    display_name: str
    ui_group: str
    activation: str
    recommended_enabled: bool

    def public_dict(self) -> dict[str, str | bool]:
        return {
            "display_name": self.display_name,
            "ui_group": self.ui_group,
            "activation": self.activation,
            "recommended_enabled": self.recommended_enabled,
        }


@dataclass(frozen=True)
class MediaScanExecutionBinding:
    pass


@dataclass(frozen=True)
class TechnicalMetricsExecutionBinding:
    pass


@dataclass(frozen=True)
class ScoringExecutionBinding:
    pass


@dataclass(frozen=True)
class StyleExecutionBinding:
    pass


@dataclass(frozen=True)
class ClusteringExecutionBinding:
    pass


@dataclass(frozen=True)
class ReviewDecisionExecutionBinding:
    pass


@dataclass(frozen=True)
class LatentResolutionExecutionBinding:
    pass


@dataclass(frozen=True)
class DatasetExportExecutionBinding:
    pass


BuiltinExecutionBinding: TypeAlias = (
    MediaScanExecutionBinding
    | TechnicalMetricsExecutionBinding
    | ScoringExecutionBinding
    | StyleExecutionBinding
    | ClusteringExecutionBinding
    | ReviewDecisionExecutionBinding
    | LatentResolutionExecutionBinding
    | DatasetExportExecutionBinding
)

_BUILTIN_EXECUTION_BINDING_TYPES = (
    MediaScanExecutionBinding,
    TechnicalMetricsExecutionBinding,
    ScoringExecutionBinding,
    StyleExecutionBinding,
    ClusteringExecutionBinding,
    ReviewDecisionExecutionBinding,
    LatentResolutionExecutionBinding,
    DatasetExportExecutionBinding,
)


@dataclass(frozen=True)
class BuiltinComponentRegistration:
    definition: ComponentDefinition
    config_model: type[BaseModel]
    ui_contract: ComponentUIContract
    execution_binding: BuiltinExecutionBinding


class BuiltinComponentRegistrationCatalog:
    def __init__(self, registrations: Iterable[BuiltinComponentRegistration]) -> None:
        self._registrations = tuple(registrations)
        self._validate_registrations()
        self._by_id = MappingProxyType(
            {
                registration.definition.manifest.id: registration
                for registration in self._registrations
            }
        )
        self._component_ids = tuple(self._by_id)
        self._definitions = tuple(
            registration.definition for registration in self._registrations
        )
        self._config_models = MappingProxyType(
            {
                component_id: registration.config_model
                for component_id, registration in self._by_id.items()
            }
        )
        self._ui_contracts = MappingProxyType(
            {
                component_id: registration.ui_contract
                for component_id, registration in self._by_id.items()
            }
        )
        self._phase_by_component = MappingProxyType(
            {
                component_id: registration.definition.manifest.task_phase
                for component_id, registration in self._by_id.items()
            }
        )
        self._execution_bindings = MappingProxyType(
            {
                component_id: registration.execution_binding
                for component_id, registration in self._by_id.items()
            }
        )

    @property
    def component_ids(self) -> tuple[str, ...]:
        return self._component_ids

    @property
    def definitions(self) -> tuple[ComponentDefinition, ...]:
        return self._definitions

    @property
    def config_models(self) -> Mapping[str, type[BaseModel]]:
        return self._config_models

    @property
    def ui_contracts(self) -> Mapping[str, ComponentUIContract]:
        return self._ui_contracts

    @property
    def phase_by_component(self) -> Mapping[str, str]:
        return self._phase_by_component

    @property
    def execution_bindings(self) -> Mapping[str, BuiltinExecutionBinding]:
        return self._execution_bindings

    def registration_for(self, component_id: str) -> BuiltinComponentRegistration:
        try:
            return self._by_id[component_id]
        except KeyError as error:
            raise ComponentRegistrationError(
                f"Unknown built-in component registration: {component_id}"
            ) from error

    def validate_component_ids(self, component_ids: Iterable[str]) -> None:
        supplied = set(component_ids)
        expected = set(self._component_ids)
        missing = sorted(expected - supplied)
        unknown = sorted(supplied - expected)
        if missing or unknown:
            raise ComponentRegistrationError(
                f"Registration coverage mismatch; missing={missing}, unknown={unknown}"
            )

    def _validate_registrations(self) -> None:
        component_ids: list[str] = []
        for registration in self._registrations:
            if not isinstance(registration, BuiltinComponentRegistration):
                raise ComponentRegistrationError("Invalid built-in component registration")
            if not isinstance(registration.definition, ComponentDefinition):
                raise ComponentRegistrationError(
                    "Component registration has an invalid definition"
                )
            component_id = registration.definition.manifest.id
            component_ids.append(component_id)
            if registration.config_model is None:
                raise ComponentRegistrationError(
                    f"Component registration {component_id} has no config model"
                )
            if not isinstance(registration.config_model, type) or not issubclass(
                registration.config_model, BaseModel
            ):
                raise ComponentRegistrationError(
                    f"Component registration {component_id} has an invalid config model"
                )
            if registration.ui_contract is None:
                raise ComponentRegistrationError(
                    f"Component registration {component_id} has no UI contract"
                )
            if not isinstance(registration.ui_contract, ComponentUIContract):
                raise ComponentRegistrationError(
                    f"Component registration {component_id} has an invalid UI contract"
                )
            if registration.execution_binding is None:
                raise ComponentRegistrationError(
                    f"Component registration {component_id} has no execution binding"
                )
            if not isinstance(
                registration.execution_binding,
                _BUILTIN_EXECUTION_BINDING_TYPES,
            ):
                raise ComponentRegistrationError(
                    f"Component registration {component_id} has an unknown execution binding"
                )

        duplicates = sorted(
            component_id
            for component_id in set(component_ids)
            if component_ids.count(component_id) > 1
        )
        if duplicates:
            raise ComponentRegistrationError(
                f"Duplicate built-in component registration ids: {duplicates}"
            )


BUILTIN_COMPONENT_REGISTRATIONS: tuple[BuiltinComponentRegistration, ...] = (
    BuiltinComponentRegistration(
        MEDIA_SCAN,
        ScanConfig,
        ComponentUIContract("媒体扫描", "input", "required", True),
        MediaScanExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        TECHNICAL_METRICS,
        EmptyComponentConfig,
        ComponentUIContract("技术指标与分辨率", "input", "required", True),
        TechnicalMetricsExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        CLIP_FEATURES,
        ClipFeatureConfig,
        ComponentUIContract("CLIP 特征", "screening", "auto", False),
        ScoringExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        AESTHETIC,
        AestheticDomainConfig,
        ComponentUIContract("美学与目标域", "screening", "optional", True),
        ScoringExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        AI_DETECTION,
        AIDetectionConfig,
        ComponentUIContract("AI 图候选", "screening", "optional", True),
        ScoringExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        OCR_EVIDENCE,
        OCREvidenceConfig,
        ComponentUIContract("OCR 风险证据", "screening", "optional", True),
        ScoringExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        WATERMARK_EVIDENCE,
        WatermarkEvidenceConfig,
        ComponentUIContract("水印证据", "screening", "optional", True),
        ScoringExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        ARTIST_STYLE,
        StyleConfig,
        ComponentUIContract("画师风格一致性", "analysis", "optional", True),
        StyleExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        SEMANTIC_EMBEDDING,
        SemanticEmbeddingConfig,
        ComponentUIContract("语义向量", "analysis", "optional", False),
        ClusteringExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        SAE_ANALYSIS,
        SparseAutoencoderConfig,
        ComponentUIContract("SAE 特征复核", "analysis", "optional", False),
        ClusteringExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        CLUSTER_HIERARCHY,
        HierarchyConfig,
        ComponentUIContract("分层聚类", "analysis", "optional", False),
        ClusteringExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        REVIEW_DECISIONS,
        EmptyComponentConfig,
        ComponentUIContract("人工复核决定", "selection", "required", True),
        ReviewDecisionExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        LATENT_RESOLVER,
        LatentConfig,
        ComponentUIContract("Latent 复用", "output", "optional", True),
        LatentResolutionExecutionBinding(),
    ),
    BuiltinComponentRegistration(
        DATASET_EXPORT,
        DatasetExportConfig,
        ComponentUIContract("数据集导出", "output", "required", True),
        DatasetExportExecutionBinding(),
    ),
)

BUILTIN_COMPONENT_REGISTRATION_CATALOG = BuiltinComponentRegistrationCatalog(
    BUILTIN_COMPONENT_REGISTRATIONS
)
