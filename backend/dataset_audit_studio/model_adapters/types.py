from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{2,79}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
EXECUTABLE_SUFFIXES = frozenset(
    {
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".exe",
        ".js",
        ".msi",
        ".ps1",
        ".py",
        ".pyc",
        ".pyd",
        ".sh",
        ".so",
    }
)
TRUSTED_DOWNLOAD_HOSTS = frozenset(
    {
        "download.pytorch.org",
        "huggingface.co",
        "openaipublic.azureedge.net",
        "raw.githubusercontent.com",
    }
)
SUPPORTED_LOADERS = frozenset(
    {
        "aesthetic_lse14_fusion_v1",
        "community_forensics_vit_small_384_v1",
        "dinov2_style_guard_v1",
        "jtp3_hydra_local_v1",
        "lsnet_kaloscope_v2_features_v1",
        "openai_clip_vit_l14_v1",
        "ppocrv5_server_det_v1",
        "ppocrv5_server_rec_v1",
        "siglip2_image_embeddings_v1",
        "siglip_watermark_classifier_v1",
        "torchvision_vgg19_gram_v1",
        "universal_fake_detector_head_v1",
        "waifu_scorer_v3_head_v1",
    }
)


class RegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size: int = Field(gt=0)
    sha256: str
    format: Literal[
        "csv",
        "json",
        "pytorch_weights_only",
        "safetensors",
        "torchscript_pinned",
        "yaml",
    ]
    url: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("Registry paths must use forward slashes")
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ValueError("Registry file path must be a safe relative path")
        if any(part.startswith(".") for part in path.parts) or path.name.endswith(".part"):
            raise ValueError("Registry file paths cannot be hidden or use the .part suffix")
        if path.suffix.casefold() in EXECUTABLE_SUFFIXES:
            raise ValueError("Executable or source-code files are forbidden in the model registry")
        return path.as_posix()

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.casefold()
        if SHA256_PATTERN.fullmatch(normalized) is None:
            raise ValueError("Model file SHA-256 must contain 64 lowercase hex characters")
        return normalized

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in TRUSTED_DOWNLOAD_HOSTS:
            raise ValueError("Model URL must use HTTPS on a fixed trusted host")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.port not in {None, 443}
        ):
            raise ValueError("Model URL must not contain credentials or a fragment")
        return value


class ModelSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["github", "huggingface", "https"]
    repository: str | None = None
    revision: str | None = None
    homepage: str
    license: str = Field(min_length=1, max_length=80)
    remote_code_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_source_identity(self) -> ModelSource:
        if self.kind in {"github", "huggingface"}:
            if self.repository is None or REPOSITORY_PATTERN.fullmatch(self.repository) is None:
                raise ValueError("Repository sources require an owner/name repository")
            if self.revision is None or REVISION_PATTERN.fullmatch(self.revision) is None:
                raise ValueError("Repository sources require a full 40-character revision")
        elif self.repository is not None or self.revision is not None:
            raise ValueError("Direct HTTPS sources cannot declare repository or revision")
        homepage = urlsplit(self.homepage)
        if homepage.scheme != "https" or homepage.hostname is None:
            raise ValueError("Model homepage must be an HTTPS URL")
        return self


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    display_name: str = Field(min_length=1, max_length=160)
    purpose: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    source: ModelSource
    files: tuple[RegistryFile, ...] = Field(min_length=1)
    loader: str
    dependencies: tuple[str, ...] = ()
    replaceable: bool = False
    replacement_schema: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if MODEL_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("Model id must be lowercase ASCII with underscores")
        return value

    @field_validator("loader")
    @classmethod
    def validate_loader(cls, value: str) -> str:
        if value not in SUPPORTED_LOADERS:
            raise ValueError(f"Unsupported model loader: {value}")
        return value

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Model dependencies must be unique")
        if any(MODEL_ID_PATTERN.fullmatch(value) is None for value in values):
            raise ValueError("Model dependency contains an invalid id")
        return values

    @model_validator(mode="after")
    def validate_files_and_replacement(self) -> ModelSpec:
        paths = [file.path for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("Model file paths must be unique")
        if self.source.kind == "huggingface" and any(file.url is not None for file in self.files):
            raise ValueError("Hugging Face file URLs are derived from the pinned revision")
        if self.source.kind != "huggingface" and any(file.url is None for file in self.files):
            raise ValueError("Non-Hugging Face files require an explicit immutable URL")
        if self.replaceable != (self.replacement_schema is not None):
            raise ValueError("Replaceable models require exactly one replacement schema")
        return self

    @property
    def total_size(self) -> int:
        return sum(file.size for file in self.files)


class RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    registry_version: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+$")
    evidence_sources: tuple[str, ...] = Field(min_length=1)
    models: tuple[ModelSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> RegistryDocument:
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("Model registry ids must be unique")
        known = set(ids)
        for model in self.models:
            unknown = set(model.dependencies) - known
            if unknown:
                raise ValueError(f"Model {model.id} has unknown dependencies: {sorted(unknown)}")
            if model.id in model.dependencies:
                raise ValueError(f"Model {model.id} cannot depend on itself")

        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {model.id: model for model in self.models}

        def visit(model_id: str) -> None:
            if model_id in visiting:
                raise ValueError(f"Model dependency cycle includes {model_id}")
            if model_id in visited:
                return
            visiting.add(model_id)
            for dependency in by_id[model_id].dependencies:
                visit(dependency)
            visiting.remove(model_id)
            visited.add(model_id)

        for model_id in ids:
            visit(model_id)
        return self


class TensorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: str
    input_dim: int
    hidden_dims: tuple[int, ...]
    has_in_domain_head: bool
    tensor_count: int
    metadata: dict[str, Any]


class InstalledFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size: int
    sha256: str
    mtime_ns: int


class InstallationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    model_id: str
    source_type: Literal["custom", "registry"]
    registry_digest: str
    registry_version: str
    base_model_id: str | None = None
    display_name: str
    loader: str
    replacement_schema: str | None = None
    original_filename: str | None = None
    source_path: str | None = None
    files: tuple[InstalledFile, ...]
    tensor_summary: TensorSummary | None = None
    verified_at: str


class FileStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size: int
    sha256: str
    present_bytes: int
    state: Literal["corrupt", "missing", "partial", "ready", "verification_required"]


class ModelStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    display_name: str
    purpose: str
    source_kind: str
    repository: str | None
    revision: str | None
    homepage: str
    license: str
    loader: str
    dependencies: tuple[str, ...]
    replaceable: bool
    replacement_schema: str | None
    remote_code_allowed: bool
    total_bytes: int
    local_root: str
    installation_status: Literal[
        "canceled",
        "corrupt",
        "downloading",
        "failed",
        "missing",
        "partial",
        "queued",
        "ready",
        "verification_required",
        "verifying",
    ]
    runtime_ready: bool
    blocking_dependencies: tuple[str, ...]
    bytes_downloaded: int
    bytes_verified: int
    current_file: str | None
    error: str | None
    verified_at: str | None
    files: tuple[FileStatus, ...]
    is_custom: bool
    base_model_id: str | None


class OperationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    operation: Literal["download", "verify"]
    status: Literal["canceled", "downloading", "failed", "queued", "ready", "verifying"]
    bytes_downloaded: int
    bytes_verified: int
    total_bytes: int
    current_file: str | None
    cancel_requested: bool
    error: str | None
    started_at: str
    updated_at: str
