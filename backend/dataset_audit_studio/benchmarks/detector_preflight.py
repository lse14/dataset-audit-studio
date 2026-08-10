from __future__ import annotations

import csv
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import torch
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from torchvision import transforms

from dataset_audit_studio.benchmarks.manifest import _normalize_sha256
from dataset_audit_studio.benchmarks.run_config import BenchmarkRunConfig
from dataset_audit_studio.core.model_assets import ModelAsset, RuntimeAssets
from dataset_audit_studio.model_adapters.registry import DEFAULT_REGISTRY
from dataset_audit_studio.model_adapters.types import (
    MODEL_ID_PATTERN,
    REPOSITORY_PATTERN,
    REVISION_PATTERN,
)
from dataset_audit_studio.model_adapters.validation import (
    inspect_safetensors,
    sha256_file,
    validate_file_container,
)

FileFormat = Literal[
    "csv",
    "json",
    "pytorch_weights_only",
    "safetensors",
    "torchscript_pinned",
    "yaml",
]
PreflightStatus = Literal["ready", "missing", "invalid"]
FileStatus = Literal["ready", "missing", "size_mismatch", "hash_mismatch", "unsafe", "extra"]
RunConfigArtifactStatus = Literal["matched", "mismatch", "not_evaluated", "not_requested"]

INSTALLATION_MANIFEST = ".installation.json"
REQUIRED_WD14_TAGS = frozenset(
    {
        "watermark",
        "signature",
        "logo",
        "artist_logo",
        "sample_watermark",
        "english_text",
        "chinese_text",
        "korean_text",
        "text_focus",
        "romaji_text",
        "mixed-language_text",
    }
)


@dataclass(frozen=True)
class CommunityForensicsPreprocessor:
    source_repository: str
    revision: str
    resize_short_edge: int
    crop_size: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]


COMMUNITY_FORENSICS_PREPROCESSOR = CommunityForensicsPreprocessor(
    source_repository="OwensLab/commfor-data-preprocessor",
    revision="3540a3f0d688f8bf492a8aed48613b891f88047e",
    resize_short_edge=440,
    crop_size=384,
    mean=(0.485, 0.456, 0.406),
    std=(0.229, 0.224, 0.225),
)


class DetectorPreflightError(RuntimeError):
    pass


class AdapterFileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size: int = Field(gt=0)
    sha256: str | None = None
    file_format: FileFormat

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("adapter asset path must use forward slashes")
        path = PurePosixPath(value)
        if not value or path.is_absolute() or "." in path.parts or ".." in path.parts:
            raise ValueError("adapter asset path must be a safe relative path")
        return path.as_posix()

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_sha256(value, label="adapter asset SHA-256")


class BenchmarkAdapterContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    source_kind: Literal["github", "huggingface"]
    source_repository: str
    revision: str
    declared_license: str = Field(min_length=1, max_length=80)
    loader: str = Field(min_length=1, max_length=120)
    dependencies: tuple[str, ...]
    remote_code_allowed: Literal[False]
    files: tuple[AdapterFileSpec, ...] = Field(min_length=1)
    root_relative: str | None
    runtime_asset_model_id: str | None

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        if MODEL_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("model_id must be lowercase ASCII with underscores")
        return value

    @field_validator("source_repository")
    @classmethod
    def validate_source_repository(cls, value: str) -> str:
        if REPOSITORY_PATTERN.fullmatch(value) is None:
            raise ValueError("source_repository must use owner/name form")
        return value

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        normalized = value.casefold()
        if REVISION_PATTERN.fullmatch(normalized) is None:
            raise ValueError("revision must contain 40 lowercase hex characters")
        return normalized

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("dependencies must be unique")
        if any(MODEL_ID_PATTERN.fullmatch(item) is None for item in value):
            raise ValueError("dependencies must use lowercase ASCII ids")
        return value

    @field_validator("root_relative")
    @classmethod
    def validate_root_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\\" in value:
            raise ValueError("root_relative must use forward slashes")
        path = PurePosixPath(value)
        if not value or path.is_absolute() or "." in path.parts or ".." in path.parts:
            raise ValueError("root_relative must be a safe relative path")
        return path.as_posix()

    @model_validator(mode="after")
    def validate_asset_source(self) -> BenchmarkAdapterContract:
        if (self.root_relative is None) == (self.runtime_asset_model_id is None):
            raise ValueError("exactly one local root or RuntimeAssets model id is required")
        paths = [file.path for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("adapter asset paths must be unique")
        return self


@dataclass(frozen=True)
class PreflightFileResult:
    path: str
    status: FileStatus
    expected_size: int | None
    actual_size: int | None
    expected_sha256: str | None
    actual_sha256: str | None
    detail: str | None = None


@dataclass(frozen=True)
class DetectorPreflightReport:
    model_id: str
    status: PreflightStatus
    root: Path | None
    files: tuple[PreflightFileResult, ...]
    errors: tuple[str, ...]
    run_config_artifacts: RunConfigArtifactStatus

    def file_path(self, relative_path: str) -> Path:
        if self.root is None or self.status != "ready":
            raise DetectorPreflightError(f"Model {self.model_id} is not ready")
        match = next((item for item in self.files if item.path == relative_path), None)
        if match is None or match.status != "ready":
            raise DetectorPreflightError(
                f"Model {self.model_id} has no ready asset file {relative_path}"
            )
        return _safe_child(self.root, relative_path)


@dataclass(frozen=True)
class LoadedCommunityForensicsAdapter:
    model: Any


@dataclass(frozen=True)
class LoadedWD14TaggerAdapter:
    model: Any
    tags: tuple[str, ...]


def _safe_child(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise DetectorPreflightError(f"Unsafe adapter asset path: {relative_path}")
    resolved_root = root.resolve(strict=False)
    candidate = root.joinpath(*relative.parts).resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise DetectorPreflightError(
            f"Adapter asset path escapes its root: {relative_path}"
        ) from error
    return candidate


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _registry_contract(model_id: str) -> BenchmarkAdapterContract:
    model = DEFAULT_REGISTRY.get(model_id)
    assert model.source.repository is not None
    assert model.source.revision is not None
    return BenchmarkAdapterContract(
        model_id=model.id,
        source_kind=model.source.kind,
        source_repository=model.source.repository,
        revision=model.source.revision,
        declared_license=model.source.license,
        loader=model.loader,
        dependencies=model.dependencies,
        remote_code_allowed=False,
        files=tuple(
            AdapterFileSpec(
                path=file.path,
                size=file.size,
                sha256=file.sha256,
                file_format=file.format,
            )
            for file in model.files
        ),
        root_relative=None,
        runtime_asset_model_id=model.id,
    )


UNIVERSAL_FAKE_DETECT_CONTRACT = _registry_contract("universal_fake_detector_head")
WATERMARK_SIGLIP2_CONTRACT = _registry_contract("watermark_siglip2")
COMMUNITY_FORENSICS_CONTRACT = BenchmarkAdapterContract(
    model_id="commfor_model_384",
    source_kind="huggingface",
    source_repository="OwensLab/commfor-model-384",
    revision="6076002bf0d9dd37537f965ee2f06f826c333b61",
    declared_license="MIT",
    loader="community_forensics_vit_small_384_v1",
    dependencies=("torch", "torchvision", "timm", "safetensors"),
    remote_code_allowed=False,
    files=(
        AdapterFileSpec(
            path="model.safetensors",
            size=87_262_324,
            sha256="b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387",
            file_format="safetensors",
        ),
    ),
    root_relative="benchmarks/commfor_model_384/6076002bf0d9dd37537f965ee2f06f826c333b61",
    runtime_asset_model_id=None,
)
WD14_EVA02_CONTRACT = BenchmarkAdapterContract(
    model_id="wd14_eva02_large_v3",
    source_kind="huggingface",
    source_repository="SmilingWolf/wd-eva02-large-tagger-v3",
    revision="b25b82a03f7282e41aa2f257a52c7583b710bd1c",
    declared_license="Apache-2.0",
    loader="wd14_eva02_large_v3",
    dependencies=("torch", "timm", "safetensors"),
    remote_code_allowed=False,
    files=(
        AdapterFileSpec(
            path="config.json",
            size=634,
            sha256="1db05aefb1a245533818e33bea22852300ac647a64636854095d1a313cd2e9dc",
            file_format="json",
        ),
        AdapterFileSpec(
            path="sw_jax_cv_config.json",
            size=469,
            sha256="9b81a8f078c929a2dd213ef59d1b8862c79e9b48c49dfd38aac136de43474e19",
            file_format="json",
        ),
        AdapterFileSpec(
            path="selected_tags.csv",
            size=308_468,
            sha256="298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217",
            file_format="csv",
        ),
        AdapterFileSpec(
            path="model.safetensors",
            size=1_260_796_004,
            sha256="74f05b0aad869d9f91fbc597bc8d157d98abdead573d5c23509a195dbb8a7ef5",
            file_format="safetensors",
        ),
    ),
    root_relative="registry/wd14_eva02_large_v3/b25b82a03f7282e41aa2f257a52c7583b710bd1c",
    runtime_asset_model_id=None,
)

_OFFICIAL_POLICIES = {
    contract.model_id: contract
    for contract in (
        UNIVERSAL_FAKE_DETECT_CONTRACT,
        WATERMARK_SIGLIP2_CONTRACT,
        COMMUNITY_FORENSICS_CONTRACT,
        WD14_EVA02_CONTRACT,
    )
}


class DetectorBenchmarkAdapter:
    def __init__(self, contract: BenchmarkAdapterContract) -> None:
        self.contract = contract

    def preflight(
        self,
        *,
        models_root: Path,
        runtime_assets: RuntimeAssets | None,
        run_config: BenchmarkRunConfig | None,
    ) -> DetectorPreflightReport:
        errors = self._validate_contract_policy()
        errors.extend(self._validate_dependency_availability(runtime_assets))
        run_model = _run_model_reference(run_config, self.contract.model_id)
        expected_files = self.contract.files
        if run_config is not None:
            errors.extend(self._validate_run_config_contract(run_model))
        root, asset, root_error = self._resolve_root(models_root, runtime_assets)
        if root_error is not None:
            errors.append(root_error)
        if root is None:
            files = tuple(
                PreflightFileResult(
                    path=file.path,
                    status="missing",
                    expected_size=file.size,
                    actual_size=None,
                    expected_sha256=file.sha256,
                    actual_sha256=None,
                )
                for file in expected_files
            )
            return DetectorPreflightReport(
                model_id=self.contract.model_id,
                status="invalid" if errors else "missing",
                root=None,
                files=files,
                errors=tuple(errors),
                run_config_artifacts="not_evaluated" if run_config is not None else "not_requested",
            )
        if asset is not None:
            errors.extend(self._validate_runtime_asset(asset))

        file_results, file_errors, has_missing, has_invalid = _verify_files(root, expected_files)
        errors.extend(file_errors)
        if not has_missing and not has_invalid and not errors:
            errors.extend(self._validate_semantics(root, expected_files))
        if any(result.status == "extra" for result in file_results):
            has_invalid = True

        run_config_artifacts: RunConfigArtifactStatus
        if run_config is None:
            run_config_artifacts = "not_requested"
        elif has_missing or has_invalid or errors:
            run_config_artifacts = "not_evaluated"
        else:
            run_config_artifacts, run_errors = self._validate_run_config(
                run_model,
                file_results,
            )
            errors.extend(run_errors)
            has_invalid = has_invalid or bool(run_errors)

        status: PreflightStatus
        if has_invalid or errors:
            status = "invalid"
        elif has_missing:
            status = "missing"
        else:
            status = "ready"
        return DetectorPreflightReport(
            model_id=self.contract.model_id,
            status=status,
            root=root,
            files=tuple(file_results),
            errors=tuple(errors),
            run_config_artifacts=run_config_artifacts,
        )

    def load(self, report: DetectorPreflightReport, **_kwargs: Any) -> Any:
        self._require_ready(report)
        raise NotImplementedError

    def _validate_contract_policy(self) -> list[str]:
        expected = _OFFICIAL_POLICIES.get(self.contract.model_id)
        if expected is None:
            return [f"unapproved benchmark model: {self.contract.model_id}"]
        errors: list[str] = []
        if self.contract.source_kind != expected.source_kind:
            errors.append("source kind does not match the approved official source")
        if self.contract.source_repository != expected.source_repository:
            errors.append("source_repository does not match the approved official repository")
        if self.contract.revision != expected.revision:
            errors.append("revision does not match the approved 40-character revision")
        if self.contract.declared_license.casefold() != expected.declared_license.casefold():
            errors.append("declared license does not match the approved model")
        if "agpl" in self.contract.declared_license.casefold():
            errors.append("AGPL models are not approved for this benchmark")
        if self.contract.loader != expected.loader:
            errors.append("loader does not match the approved adapter")
        if self.contract.dependencies != expected.dependencies:
            errors.append("dependencies do not match the approved adapter")
        if self.contract.remote_code_allowed is not False:
            errors.append("remote_code_allowed must be false")
        if self.contract.root_relative != expected.root_relative:
            errors.append("root_relative does not match the approved adapter")
        if self.contract.runtime_asset_model_id != expected.runtime_asset_model_id:
            errors.append("runtime_asset_model_id does not match the approved adapter")
        if len(self.contract.files) != len(expected.files):
            errors.append("files do not match the approved adapter")
        else:
            for index, (actual_file, expected_file) in enumerate(
                zip(self.contract.files, expected.files, strict=True)
            ):
                if actual_file.path != expected_file.path:
                    errors.append(f"approved file path mismatch at index {index}")
                if actual_file.size != expected_file.size:
                    errors.append(f"approved file size mismatch at index {index}")
                if actual_file.sha256 != expected_file.sha256:
                    errors.append(f"approved file SHA-256 mismatch at index {index}")
                if actual_file.file_format != expected_file.file_format:
                    errors.append(f"approved file format mismatch at index {index}")
        return errors

    def _validate_dependency_availability(
        self,
        runtime_assets: RuntimeAssets | None,
    ) -> list[str]:
        if self.contract.runtime_asset_model_id is not None:
            if runtime_assets is None:
                return []
            errors: list[str] = []
            for dependency in self.contract.dependencies:
                try:
                    runtime_assets.get(dependency)
                except KeyError:
                    errors.append(f"RuntimeAssets snapshot is missing dependency: {dependency}")
            return errors
        return [
            f"approved dependency is unavailable: {dependency}"
            for dependency in self.contract.dependencies
            if importlib.util.find_spec(dependency) is None
        ]

    def _resolve_root(
        self,
        models_root: Path,
        runtime_assets: RuntimeAssets | None,
    ) -> tuple[Path | None, ModelAsset | None, str | None]:
        root_base = models_root.resolve(strict=False)
        if self.contract.runtime_asset_model_id is not None:
            if runtime_assets is None:
                return None, None, "RuntimeAssets is required for this baseline"
            try:
                asset = runtime_assets.get(self.contract.runtime_asset_model_id)
            except KeyError:
                return None, None, "RuntimeAssets snapshot is missing the baseline model"
            root = Path(asset.root).resolve(strict=False)
            if not root.exists():
                return None, asset, "RuntimeAssets baseline root is missing"
            if not _under_root(root, root_base):
                return None, asset, "RuntimeAssets baseline root escapes models_root"
            return root, asset, None

        assert self.contract.root_relative is not None
        root = _safe_child(root_base, self.contract.root_relative)
        if root.exists():
            return root, None, None
        parent = root.parent
        if parent.exists() and any(item.is_dir() for item in parent.iterdir()):
            return None, None, "revision mismatch: candidate asset exists under another revision"
        return None, None, None

    def _validate_runtime_asset(self, asset: ModelAsset) -> list[str]:
        errors: list[str] = []
        if asset.model_id != self.contract.model_id:
            errors.append("RuntimeAssets model id does not match adapter contract")
        if asset.loader != self.contract.loader:
            errors.append("RuntimeAssets loader does not match adapter contract")
        if asset.dependencies != self.contract.dependencies:
            errors.append("RuntimeAssets dependencies do not match adapter contract")
        expected = {(file.path, file.size, file.sha256) for file in self.contract.files}
        observed = {(file.path, file.size, file.sha256) for file in asset.files}
        if observed != expected:
            errors.append("RuntimeAssets snapshot does not match adapter contract files")
        return errors

    def _validate_run_config_contract(self, run_model: Any) -> list[str]:
        if run_model is None:
            return ["run-config does not include this approved model"]
        if not self._matches_run_model_identity(run_model):
            return ["run-config model identity does not match the approved contract"]
        approved_hashes = tuple(
            sorted(file.sha256 for file in self.contract.files if file.sha256 is not None)
        )
        if len(approved_hashes) != len(self.contract.files):
            return ["approved contract must pin every file SHA-256"]
        if tuple(sorted(run_model.artifact_sha256)) != approved_hashes:
            return ["run-config artifact hash does not match the approved contract"]
        return []

    def _validate_semantics(
        self,
        _root: Path,
        _files: tuple[AdapterFileSpec, ...],
    ) -> list[str]:
        return []

    def _validate_run_config(
        self,
        run_model: Any,
        files: list[PreflightFileResult],
    ) -> tuple[RunConfigArtifactStatus, list[str]]:
        if run_model is None:
            return "mismatch", ["run-config does not include this approved model"]
        identity = self._matches_run_model_identity(run_model)
        actual_hashes = tuple(
            sorted(result.actual_sha256 for result in files if result.actual_sha256 is not None)
        )
        configured_hashes = tuple(sorted(run_model.artifact_sha256))
        if not identity or actual_hashes != configured_hashes:
            return "mismatch", ["run-config artifact hash or model identity mismatch"]
        return "matched", []

    def _matches_run_model_identity(self, run_model: Any) -> bool:
        return (
            run_model.source_kind == self.contract.source_kind
            and run_model.source_repository == self.contract.source_repository
            and run_model.revision == self.contract.revision
            and run_model.declared_license.casefold() == self.contract.declared_license.casefold()
            and run_model.remote_code_allowed is False
        )

    @staticmethod
    def _require_ready(report: DetectorPreflightReport) -> None:
        if report.status != "ready":
            raise DetectorPreflightError(f"Model {report.model_id} is not ready for adapter load")


class UniversalFakeDetectAdapter(DetectorBenchmarkAdapter):
    def load(
        self,
        report: DetectorPreflightReport,
        *,
        config: Any,
        runtime_assets: RuntimeAssets,
    ) -> Any:
        self._require_ready(report)
        from dataset_audit_studio.components.ai_detection.runtime import AIDetectionRuntime

        return AIDetectionRuntime(config, runtime_assets)


class WatermarkSiglip2Adapter(DetectorBenchmarkAdapter):
    def load(
        self,
        report: DetectorPreflightReport,
        *,
        config: Any,
        runtime_assets: RuntimeAssets,
    ) -> Any:
        self._require_ready(report)
        from dataset_audit_studio.components.watermark_evidence.runtime import (
            WatermarkEvidenceRuntime,
        )

        return WatermarkEvidenceRuntime(config, runtime_assets)


def preprocess_community_forensics_image(image: Image.Image) -> torch.Tensor:
    """Apply the fixed Community Forensics test transform without remote code."""
    processor = COMMUNITY_FORENSICS_PREPROCESSOR
    transform = transforms.Compose(
        (
            transforms.Resize(processor.resize_short_edge),
            transforms.CenterCrop(processor.crop_size),
            transforms.ToTensor(),
            transforms.Normalize(mean=processor.mean, std=processor.std),
        )
    )
    return transform(image.convert("RGB"))


def community_forensics_raw_sigmoid_scores(logits: torch.Tensor) -> list[dict[str, float]]:
    """Expose one uncalibrated raw sigmoid score for each single-logit output."""
    if logits.ndim != 2 or logits.shape[1] != 1:
        raise ValueError("Community Forensics output must contain one single logit per image")
    return [{"raw_sigmoid_score": float(score)} for score in torch.sigmoid(logits[:, 0])]


class CommunityForensicsClassifier(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        import timm

        self.vit = timm.create_model(
            "vit_small_patch16_384.augreg_in21k_ft_in1k",
            pretrained=False,
        )
        self.vit.head = torch.nn.Linear(384, 1)

    def forward(self, pixels: torch.Tensor) -> torch.Tensor:
        return self.vit(pixels)


class CommunityForensicsAdapter(DetectorBenchmarkAdapter):
    def _validate_semantics(
        self,
        root: Path,
        _files: tuple[AdapterFileSpec, ...],
    ) -> list[str]:
        _, shapes = inspect_safetensors(root / "model.safetensors")
        if not shapes or any(not name.startswith("vit.") for name in shapes):
            return ["Community Forensics safetensors must contain only vit.* state-dict keys"]
        weight = shapes.get("vit.head.weight")
        bias = shapes.get("vit.head.bias")
        if weight is None or bias is None or len(weight) != 2 or bias != (weight[0],):
            return [
                "Community Forensics safetensors must define matching "
                "vit.head.weight and vit.head.bias"
            ]
        if weight != (1, 384) or bias != (1,):
            return [
                "Community Forensics vit.head must have one output logit with ViT-Small width 384"
            ]
        return []

    def load(
        self, report: DetectorPreflightReport, **_kwargs: Any
    ) -> LoadedCommunityForensicsAdapter:
        self._require_ready(report)
        from safetensors.torch import load_file

        path = report.file_path("model.safetensors")
        model = CommunityForensicsClassifier()
        state = load_file(str(path), device="cpu")
        model.load_state_dict(state, strict=True)
        return LoadedCommunityForensicsAdapter(
            model=model.eval().requires_grad_(False),
        )


class WD14TaggerAdapter(DetectorBenchmarkAdapter):
    def _validate_semantics(
        self,
        root: Path,
        _files: tuple[AdapterFileSpec, ...],
    ) -> list[str]:
        try:
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            return [f"WD14 config is invalid: {error}"]
        if not isinstance(config, dict):
            return ["WD14 config must contain an object"]
        if config.get("architecture") != "eva02_large_patch14_448":
            return ["WD14 architecture does not match eva02_large_patch14_448"]
        try:
            num_classes = int(config["num_classes"])
        except (KeyError, TypeError, ValueError):
            return ["WD14 config requires integer num_classes"]
        tags, tag_errors = _read_wd14_tags(root / "selected_tags.csv")
        if tag_errors:
            return tag_errors
        _, shapes = inspect_safetensors(root / "model.safetensors")
        weight = shapes.get("head.weight")
        bias = shapes.get("head.bias")
        if weight is None or bias is None or len(weight) != 2 or bias != (weight[0],):
            return ["WD14 safetensors must define matching head.weight and head.bias"]
        if weight[0] != num_classes or len(tags) != num_classes:
            return [
                "WD14 output dimension does not match config num_classes and selected_tags rows"
            ]
        return []

    def load(self, report: DetectorPreflightReport, **_kwargs: Any) -> LoadedWD14TaggerAdapter:
        self._require_ready(report)
        import timm
        from safetensors.torch import load_file

        config = json.loads(report.file_path("config.json").read_text(encoding="utf-8"))
        tags, tag_errors = _read_wd14_tags(report.file_path("selected_tags.csv"))
        if tag_errors:
            raise DetectorPreflightError("WD14 tag validation changed after preflight")
        path = report.file_path("model.safetensors")
        model = timm.create_model(
            str(config["architecture"]),
            pretrained=False,
            num_classes=int(config["num_classes"]),
        )
        model.load_state_dict(load_file(str(path), device="cpu"), strict=True)
        return LoadedWD14TaggerAdapter(
            model=model.eval().requires_grad_(False),
            tags=tags,
        )


def load_ppocrv5_auxiliary_evidence(*, config: Any, runtime_assets: RuntimeAssets) -> Any:
    from dataset_audit_studio.components.ocr_evidence.runtime import OCREvidenceRuntime

    return OCREvidenceRuntime(config, runtime_assets)


def _run_model_reference(
    run_config: BenchmarkRunConfig | None,
    model_id: str,
) -> Any | None:
    if run_config is None:
        return None
    return next((model for model in run_config.models if model.model_id == model_id), None)


def _verify_files(
    root: Path,
    files: tuple[AdapterFileSpec, ...],
) -> tuple[list[PreflightFileResult], list[str], bool, bool]:
    results: list[PreflightFileResult] = []
    errors: list[str] = []
    has_missing = False
    has_invalid = False
    for expected in files:
        path = _safe_child(root, expected.path)
        if not path.exists():
            has_missing = True
            results.append(
                PreflightFileResult(
                    path=expected.path,
                    status="missing",
                    expected_size=expected.size,
                    actual_size=None,
                    expected_sha256=expected.sha256,
                    actual_sha256=None,
                )
            )
            continue
        if path.is_symlink() or not path.is_file():
            has_invalid = True
            errors.append(f"unsafe asset file: {expected.path}")
            results.append(
                PreflightFileResult(
                    path=expected.path,
                    status="unsafe",
                    expected_size=expected.size,
                    actual_size=None,
                    expected_sha256=expected.sha256,
                    actual_sha256=None,
                )
            )
            continue
        actual_size = path.stat().st_size
        if actual_size != expected.size:
            has_invalid = True
            errors.append(f"size mismatch: {expected.path}")
            results.append(
                PreflightFileResult(
                    path=expected.path,
                    status="size_mismatch",
                    expected_size=expected.size,
                    actual_size=actual_size,
                    expected_sha256=expected.sha256,
                    actual_sha256=None,
                )
            )
            continue
        actual_sha256 = sha256_file(path)
        if expected.sha256 is None:
            has_invalid = True
            errors.append(f"pinned SHA-256 is missing: {expected.path}")
            results.append(
                PreflightFileResult(
                    path=expected.path,
                    status="hash_mismatch",
                    expected_size=expected.size,
                    actual_size=actual_size,
                    expected_sha256=None,
                    actual_sha256=actual_sha256,
                )
            )
            continue
        if actual_sha256 != expected.sha256:
            has_invalid = True
            errors.append(f"hash mismatch: {expected.path}")
            results.append(
                PreflightFileResult(
                    path=expected.path,
                    status="hash_mismatch",
                    expected_size=expected.size,
                    actual_size=actual_size,
                    expected_sha256=expected.sha256,
                    actual_sha256=actual_sha256,
                )
            )
            continue
        try:
            validate_file_container(path, expected.file_format)
        except RuntimeError as error:
            has_invalid = True
            errors.append(f"invalid asset container: {expected.path}: {error}")
            results.append(
                PreflightFileResult(
                    path=expected.path,
                    status="unsafe",
                    expected_size=expected.size,
                    actual_size=actual_size,
                    expected_sha256=expected.sha256,
                    actual_sha256=actual_sha256,
                    detail=str(error),
                )
            )
            continue
        results.append(
            PreflightFileResult(
                path=expected.path,
                status="ready",
                expected_size=expected.size,
                actual_size=actual_size,
                expected_sha256=expected.sha256,
                actual_sha256=actual_sha256,
            )
        )

    expected_paths = {file.path for file in files}
    allowed_paths = expected_paths | {INSTALLATION_MANIFEST}
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in allowed_paths:
            continue
        has_invalid = True
        errors.append(f"extra asset file: {relative}")
        results.append(
            PreflightFileResult(
                path=relative,
                status="extra",
                expected_size=None,
                actual_size=path.stat().st_size if path.exists() else None,
                expected_sha256=None,
                actual_sha256=None,
            )
        )
    return results, errors, has_missing, has_invalid


def _read_wd14_tags(path: Path) -> tuple[tuple[str, ...], list[str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or "name" not in reader.fieldnames:
                return (), ["WD14 selected_tags.csv requires a name column"]
            tags = tuple(str(row.get("name", "")).strip() for row in reader)
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        return (), [f"WD14 selected_tags.csv is invalid: {error}"]
    if not tags or any(not tag for tag in tags):
        return (), ["WD14 selected_tags.csv contains an empty tag"]
    if len(tags) != len(set(tags)):
        return (), ["WD14 duplicate tag in selected_tags.csv"]
    missing = sorted(REQUIRED_WD14_TAGS - set(tags))
    if missing:
        return (), [f"WD14 missing tag(s): {missing}"]
    return tags, []


DEFAULT_DETECTOR_ADAPTERS: tuple[DetectorBenchmarkAdapter, ...] = (
    UniversalFakeDetectAdapter(UNIVERSAL_FAKE_DETECT_CONTRACT),
    WatermarkSiglip2Adapter(WATERMARK_SIGLIP2_CONTRACT),
    CommunityForensicsAdapter(COMMUNITY_FORENSICS_CONTRACT),
    WD14TaggerAdapter(WD14_EVA02_CONTRACT),
)
