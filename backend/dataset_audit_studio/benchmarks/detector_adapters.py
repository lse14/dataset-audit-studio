"""Offline benchmark adapters for the B3.3 harness lifecycle.

The adapters deliberately keep model construction injectable. Production uses the
already-pinned local runtimes while B3.4/B3.5 tests use tiny fake tensors and
loaders.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from typing import Any, cast

import torch
from PIL import Image

from dataset_audit_studio.benchmarks.detector_preflight import (
    COMMUNITY_FORENSICS_CONTRACT,
    COMMUNITY_FORENSICS_PREPROCESSOR,
    REQUIRED_WD14_TAGS,
    WATERMARK_SIGLIP2_CONTRACT,
    WD14_EVA02_CONTRACT,
    DetectorPreflightReport,
    community_forensics_raw_sigmoid_scores,
    load_ppocrv5_auxiliary_evidence,
    preprocess_community_forensics_image,
)
from dataset_audit_studio.benchmarks.detector_preflight import (
    CommunityForensicsAdapter as CommunityForensicsPreflightAdapter,
)
from dataset_audit_studio.benchmarks.detector_preflight import (
    WD14TaggerAdapter as WD14TaggerPreflightAdapter,
)
from dataset_audit_studio.benchmarks.run_config import (
    OCR_DETECTOR_MODEL_ID,
    OCR_RECOGNIZER_MODEL_ID,
)
from dataset_audit_studio.benchmarks.sidecar import (
    BenchmarkSidecarAsset,
    BenchmarkSidecarAssetFile,
    BenchmarkSidecarAuxiliaryOcrProvenance,
    BenchmarkSidecarDetectorProvenance,
    BenchmarkSidecarPreprocessingSource,
)
from dataset_audit_studio.components.ai_detection.runtime import AI_MODEL_ID
from dataset_audit_studio.components.clip_features.runtime import CLIP_MODEL_ID
from dataset_audit_studio.core.model_assets import (
    ModelAsset,
    RuntimeAssets,
    select_runtime_assets,
)
from dataset_audit_studio.core.torch_runtime import (
    DeviceRequest,
    Precision,
    autocast_context,
    release_torch_memory,
    resolve_torch_device,
)


def _default_clip_runtime_factory(
    assets: RuntimeAssets,
    requested_device: str,
    requested_precision: Precision,
) -> Any:
    from dataset_audit_studio.components.clip_features.config import ClipFeatureConfig
    from dataset_audit_studio.components.clip_features.runtime import ClipFeatureRuntime

    return ClipFeatureRuntime(
        ClipFeatureConfig(
            device=requested_device,
            precision=requested_precision,
            batch_size=1,
        ),
        assets,
    )


def _default_head_runtime_factory(
    assets: RuntimeAssets,
    requested_device: str,
    requested_precision: Precision,
) -> Any:
    from dataset_audit_studio.components.ai_detection.config import AIDetectionConfig
    from dataset_audit_studio.components.ai_detection.runtime import AIDetectionRuntime

    return AIDetectionRuntime(
        AIDetectionConfig(
            device=requested_device,
            precision=requested_precision,
            model_id=AI_MODEL_ID,
        ),
        assets,
    )


def _default_watermark_runtime_factory(
    assets: RuntimeAssets,
    requested_device: str,
    requested_precision: Precision,
) -> Any:
    from dataset_audit_studio.components.watermark_evidence.config import (
        WatermarkEvidenceConfig,
    )
    from dataset_audit_studio.components.watermark_evidence.runtime import (
        WatermarkEvidenceRuntime,
    )

    return WatermarkEvidenceRuntime(
        WatermarkEvidenceConfig(
            device=cast(DeviceRequest, requested_device),
            precision=requested_precision,
        ),
        assets,
    )


def _default_ocr_runtime_factory(
    assets: RuntimeAssets,
    requested_device: str,
    requested_precision: Precision,
    recognizer_batch_size: int,
) -> Any:
    from dataset_audit_studio.components.ocr_evidence.config import OCREvidenceConfig

    return load_ppocrv5_auxiliary_evidence(
        config=OCREvidenceConfig(
            device=cast(DeviceRequest, requested_device),
            precision=requested_precision,
            recognition_batch_size=recognizer_batch_size,
        ),
        runtime_assets=assets,
    )


def _require_ready_matched_report(
    report: DetectorPreflightReport,
    *,
    model_id: str,
) -> None:
    if report.model_id != model_id:
        raise RuntimeError(f"preflight report does not belong to {model_id}")
    if report.status != "ready" or report.run_config_artifacts != "matched":
        raise RuntimeError(
            f"{model_id} load requires a ready preflight report with matched run-config artifacts"
        )


def _runtime_asset_to_sidecar(asset: ModelAsset, *, role: str) -> BenchmarkSidecarAsset:
    return BenchmarkSidecarAsset(
        asset_id=asset.model_id,
        role=role,  # type: ignore[arg-type]
        files=tuple(
            BenchmarkSidecarAssetFile(
                path=file.path,
                sha256=file.sha256,
                size_bytes=file.size,
            )
            for file in asset.files
        ),
    )


def _preflight_asset_to_sidecar(
    report: DetectorPreflightReport,
    *,
    model_id: str,
) -> BenchmarkSidecarAsset:
    _require_ready_matched_report(report, model_id=model_id)
    files: list[BenchmarkSidecarAssetFile] = []
    for file in report.files:
        if (
            file.status != "ready"
            or file.actual_size is None
            or file.actual_sha256 is None
        ):
            raise RuntimeError(f"{model_id} preflight has no ready asset file {file.path}")
        files.append(
            BenchmarkSidecarAssetFile(
                path=file.path,
                sha256=file.actual_sha256,
                size_bytes=file.actual_size,
            )
        )
    return BenchmarkSidecarAsset(asset_id=model_id, role="model", files=tuple(files))


def _require_runtime_asset_matches_preflight(
    asset: ModelAsset,
    report: DetectorPreflightReport,
) -> None:
    expected = {
        (file.path, file.actual_size, file.actual_sha256)
        for file in report.files
        if file.status == "ready"
        and file.actual_size is not None
        and file.actual_sha256 is not None
    }
    observed = {(file.path, file.size, file.sha256) for file in asset.files}
    if expected != observed:
        raise RuntimeError(
            f"{asset.model_id} RuntimeAssets files do not match the ready preflight report"
        )


def _stack_preprocessed_images(
    images: Sequence[Image.Image],
    transform: Callable[[Image.Image], torch.Tensor],
) -> torch.Tensor:
    if not images:
        raise ValueError("detector adapter batch must not be empty")
    return torch.stack([transform(image.convert("RGB")) for image in images])


def _to_cpu_raw_sigmoid_scores(
    outputs: Any,
    *,
    model_id: str,
) -> tuple[Mapping[str, float], ...]:
    if not isinstance(outputs, torch.Tensor):
        raise ValueError(f"{model_id} output must be a tensor")
    logits = outputs.detach().to("cpu")
    if logits.ndim != 2 or logits.shape[1] != 1:
        raise ValueError(f"{model_id} output must contain one single logit per image")
    return tuple(
        {"raw_sigmoid_score": float(score)} for score in torch.sigmoid(logits[:, 0]).tolist()
    )


def preprocess_wd14_image(image: Image.Image) -> torch.Tensor:
    """Apply the fixed local WD14 448/bicubic/center/0.5 transform."""
    from torchvision.transforms import InterpolationMode
    from torchvision.transforms import functional as transforms_functional

    rgb_image = image.convert("RGB")
    resized = transforms_functional.resize(
        rgb_image,
        size=448,
        interpolation=InterpolationMode.BICUBIC,
    )
    cropped = transforms_functional.center_crop(resized, output_size=(448, 448))
    pixels = transforms_functional.to_tensor(cropped)
    return transforms_functional.normalize(
        pixels,
        mean=(0.5, 0.5, 0.5),
        std=(0.5, 0.5, 0.5),
    )


def _close_runtime_references(*references: Any) -> None:
    first_error: BaseException | None = None
    for reference in references:
        if reference is None:
            continue
        close = getattr(reference, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except BaseException as error:  # Preserve a caller's original failure when present.
            if first_error is None:
                first_error = error
    release_torch_memory()
    if first_error is not None:
        raise first_error


class UniversalFakeDetectBenchmarkAdapter:
    """Run the pinned OpenAI CLIP feature extractor and UFD head per batch."""

    model_id = AI_MODEL_ID

    def __init__(
        self,
        *,
        runtime_assets: RuntimeAssets,
        clip_runtime_factory: Callable[[RuntimeAssets, str, Precision], Any] = (
            _default_clip_runtime_factory
        ),
        head_runtime_factory: Callable[[RuntimeAssets, str, Precision], Any] = (
            _default_head_runtime_factory
        ),
    ) -> None:
        self._runtime_assets = runtime_assets
        self._clip_runtime_factory = clip_runtime_factory
        self._head_runtime_factory = head_runtime_factory
        self._clip_runtime: Any | None = None
        self._head_runtime: Any | None = None
        self._execution_device: str | None = None
        self._execution_precision: Precision | None = None
        self._provenance: BenchmarkSidecarDetectorProvenance | None = None

    @property
    def provenance(self) -> BenchmarkSidecarDetectorProvenance:
        if self._provenance is None:
            raise RuntimeError(f"{self.model_id} adapter is not loaded")
        return self._provenance

    def load(
        self,
        *,
        preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: Precision,
    ) -> None:
        _require_ready_matched_report(preflight_report, model_id=self.model_id)
        if self._clip_runtime is not None or self._head_runtime is not None:
            raise RuntimeError(f"{self.model_id} adapter is already loaded")

        try:
            selected_assets = select_runtime_assets(
                self._runtime_assets,
                (self.model_id, CLIP_MODEL_ID),
            )
        except KeyError as error:
            raise RuntimeError(
                f"{self.model_id} requires the {CLIP_MODEL_ID} dependency asset"
            ) from error
        head_asset = selected_assets.get(self.model_id)
        clip_asset = selected_assets.get(CLIP_MODEL_ID)
        _require_runtime_asset_matches_preflight(head_asset, preflight_report)
        try:
            self._clip_runtime = self._clip_runtime_factory(
                selected_assets,
                requested_device,
                requested_precision,
            )
            self._head_runtime = self._head_runtime_factory(
                selected_assets,
                requested_device,
                requested_precision,
            )
            self._provenance = BenchmarkSidecarDetectorProvenance(
                model_id=self.model_id,
                assets=(
                    _runtime_asset_to_sidecar(head_asset, role="model"),
                    _runtime_asset_to_sidecar(clip_asset, role="dependency"),
                ),
                preprocessing=BenchmarkSidecarPreprocessingSource(
                    source="WisconsinAIVision/UniversalFakeDetect",
                    revision="76a0e3e60a8a06458707a625d269ba815a2e5919",
                ),
            )
        except BaseException:
            with suppress(BaseException):
                self.close()
            raise

    def preprocess(self, images: Sequence[Image.Image]) -> torch.Tensor:
        clip_runtime = self._require_clip_runtime()
        return _stack_preprocessed_images(images, clip_runtime.ufd_preprocess)

    def transfer_to_device(
        self,
        prepared: torch.Tensor,
        *,
        device: str,
        precision: Precision,
    ) -> torch.Tensor:
        self._require_clip_runtime()
        self._require_head_runtime()
        self._execution_device = device
        self._execution_precision = precision
        return prepared.to(device)

    def forward(self, transferred: torch.Tensor) -> torch.Tensor:
        clip_runtime = self._require_clip_runtime()
        head_runtime = self._require_head_runtime()
        if self._execution_device is None or self._execution_precision is None:
            raise RuntimeError(f"{self.model_id} inputs were not transferred to a device")
        with torch.inference_mode(), autocast_context(
            torch.device(self._execution_device), self._execution_precision
        ):
            features = clip_runtime.model.encode_image(transferred).float()
            if features.ndim != 2 or features.shape[1] != 768:
                raise ValueError(f"{self.model_id} CLIP features must have shape (batch, 768)")
            return head_runtime.head(features)

    def to_cpu(self, outputs: Any) -> tuple[Mapping[str, float], ...]:
        self._require_head_runtime()
        return _to_cpu_raw_sigmoid_scores(outputs, model_id=self.model_id)

    def close(self) -> None:
        head_runtime = self._head_runtime
        clip_runtime = self._clip_runtime
        self._head_runtime = None
        self._clip_runtime = None
        self._execution_device = None
        self._execution_precision = None
        self._provenance = None
        _close_runtime_references(head_runtime, clip_runtime)

    def _require_clip_runtime(self) -> Any:
        if self._clip_runtime is None:
            raise RuntimeError(f"{self.model_id} CLIP runtime is not loaded")
        return self._clip_runtime

    def _require_head_runtime(self) -> Any:
        if self._head_runtime is None:
            raise RuntimeError(f"{self.model_id} head runtime is not loaded")
        return self._head_runtime


class CommunityForensicsBenchmarkAdapter:
    """Run the B2.1 local Community Forensics wrapper inside the harness."""

    model_id = COMMUNITY_FORENSICS_CONTRACT.model_id

    def __init__(
        self,
        *,
        community_loader: Any | None = None,
        preprocess_image: Callable[[Image.Image], torch.Tensor] | None = None,
        raw_score_converter: Callable[[torch.Tensor], Sequence[Mapping[str, float]]] = (
            community_forensics_raw_sigmoid_scores
        ),
    ) -> None:
        self._community_loader = community_loader or CommunityForensicsPreflightAdapter(
            COMMUNITY_FORENSICS_CONTRACT
        )
        self._preprocess_image = preprocess_image or preprocess_community_forensics_image
        self._raw_score_converter = raw_score_converter
        self._model: Any | None = None
        self._execution_device: str | None = None
        self._execution_precision: Precision | None = None
        self._provenance: BenchmarkSidecarDetectorProvenance | None = None

    @property
    def provenance(self) -> BenchmarkSidecarDetectorProvenance:
        if self._provenance is None:
            raise RuntimeError(f"{self.model_id} adapter is not loaded")
        return self._provenance

    def load(
        self,
        *,
        preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: Precision,
    ) -> None:
        _require_ready_matched_report(preflight_report, model_id=self.model_id)
        if self._model is not None:
            raise RuntimeError(f"{self.model_id} adapter is already loaded")
        if self._community_loader is None:
            raise RuntimeError(f"{self.model_id} adapter has been closed")
        try:
            loaded = self._community_loader.load(preflight_report)
            model = loaded.model
            if model is None:
                raise RuntimeError(f"{self.model_id} B2.1 loader returned no model")
            self._model = model
            move = getattr(model, "to", None)
            if callable(move):
                device = resolve_torch_device(
                    cast(DeviceRequest, requested_device),
                    requested_precision,
                )
                moved = move(device)
                if moved is not None:
                    model = moved
            self._model = model
            self._provenance = BenchmarkSidecarDetectorProvenance(
                model_id=self.model_id,
                assets=(_preflight_asset_to_sidecar(preflight_report, model_id=self.model_id),),
                preprocessing=BenchmarkSidecarPreprocessingSource(
                    source=COMMUNITY_FORENSICS_PREPROCESSOR.source_repository,
                    revision=COMMUNITY_FORENSICS_PREPROCESSOR.revision,
                ),
            )
        except BaseException:
            with suppress(BaseException):
                self.close()
            raise

    def preprocess(self, images: Sequence[Image.Image]) -> torch.Tensor:
        if self._preprocess_image is None:
            raise RuntimeError(f"{self.model_id} adapter is closed")
        self._require_model()
        return _stack_preprocessed_images(images, self._preprocess_image)

    def transfer_to_device(
        self,
        prepared: torch.Tensor,
        *,
        device: str,
        precision: Precision,
    ) -> torch.Tensor:
        self._require_model()
        self._execution_device = device
        self._execution_precision = precision
        return prepared.to(device)

    def forward(self, transferred: torch.Tensor) -> Any:
        model = self._require_model()
        if self._execution_device is None or self._execution_precision is None:
            raise RuntimeError(f"{self.model_id} inputs were not transferred to a device")
        with torch.inference_mode(), autocast_context(
            torch.device(self._execution_device), self._execution_precision
        ):
            return model(transferred)

    def to_cpu(self, outputs: Any) -> tuple[Mapping[str, float], ...]:
        self._require_model()
        if not isinstance(outputs, torch.Tensor):
            raise ValueError(f"{self.model_id} output must be a tensor")
        logits = outputs.detach().to("cpu")
        return tuple(self._raw_score_converter(logits))

    def close(self) -> None:
        model = self._model
        self._model = None
        self._community_loader = None
        self._preprocess_image = None
        self._execution_device = None
        self._execution_precision = None
        self._provenance = None
        _close_runtime_references(model)

    def _require_model(self) -> Any:
        if self._model is None:
            raise RuntimeError(f"{self.model_id} model is not loaded")
        return self._model


class WatermarkSiglip2BenchmarkAdapter:
    """Run the pinned local Watermark-SigLIP2 classifier per harness batch."""

    model_id = WATERMARK_SIGLIP2_CONTRACT.model_id

    def __init__(
        self,
        *,
        runtime_assets: RuntimeAssets,
        runtime_factory: Callable[[RuntimeAssets, str, Precision], Any] = (
            _default_watermark_runtime_factory
        ),
    ) -> None:
        self._runtime_assets = runtime_assets
        self._runtime_factory = runtime_factory
        self._runtime: Any | None = None
        self._labels: dict[int, str] | None = None
        self._watermark_index: int | None = None
        self._execution_device: str | None = None
        self._execution_precision: Precision | None = None
        self._provenance: BenchmarkSidecarDetectorProvenance | None = None

    @property
    def provenance(self) -> BenchmarkSidecarDetectorProvenance:
        if self._provenance is None:
            raise RuntimeError(f"{self.model_id} adapter is not loaded")
        return self._provenance

    def load(
        self,
        *,
        preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: Precision,
    ) -> None:
        _require_ready_matched_report(preflight_report, model_id=self.model_id)
        if self._runtime is not None:
            raise RuntimeError(f"{self.model_id} adapter is already loaded")
        try:
            selected_assets = select_runtime_assets(self._runtime_assets, (self.model_id,))
        except KeyError as error:
            raise RuntimeError(f"{self.model_id} requires its RuntimeAssets model asset") from error
        asset = selected_assets.get(self.model_id)
        _require_runtime_asset_matches_preflight(asset, preflight_report)
        try:
            resolved_device = resolve_torch_device(
                cast(DeviceRequest, requested_device),
                requested_precision,
            )
            runtime = self._runtime_factory(
                selected_assets,
                str(resolved_device),
                requested_precision,
            )
            self._runtime = runtime
            labels = _watermark_labels(runtime)
            matches = [
                index
                for index, label in labels.items()
                if label.casefold().replace("_", " ").strip() == "watermark"
            ]
            if len(matches) != 1:
                raise RuntimeError("Watermark model config must define exactly one Watermark label")
            self._labels = labels
            self._watermark_index = matches[0]
            self._provenance = BenchmarkSidecarDetectorProvenance(
                model_id=self.model_id,
                assets=(_runtime_asset_to_sidecar(asset, role="model"),),
                preprocessing=BenchmarkSidecarPreprocessingSource(
                    source=WATERMARK_SIGLIP2_CONTRACT.source_repository,
                    revision=WATERMARK_SIGLIP2_CONTRACT.revision,
                ),
            )
        except BaseException:
            with suppress(BaseException):
                self.close()
            raise

    def preprocess(self, images: Sequence[Image.Image]) -> Any:
        runtime = self._require_runtime()
        processor = getattr(runtime, "processor", None)
        if not callable(processor):
            raise RuntimeError(f"{self.model_id} processor is not loaded")
        return processor(
            images=[image.convert("RGB") for image in images],
            return_tensors="pt",
        )

    def transfer_to_device(
        self,
        prepared: Any,
        *,
        device: str,
        precision: Precision,
    ) -> Any:
        self._require_runtime()
        move = getattr(prepared, "to", None)
        if not callable(move):
            raise RuntimeError(f"{self.model_id} processor output cannot move to a device")
        self._execution_device = device
        self._execution_precision = precision
        return move(device)

    def forward(self, transferred: Any) -> torch.Tensor:
        runtime = self._require_runtime()
        if self._execution_device is None or self._execution_precision is None:
            raise RuntimeError(f"{self.model_id} inputs were not transferred to a device")
        model = getattr(runtime, "model", None)
        if not callable(model):
            raise RuntimeError(f"{self.model_id} model is not loaded")
        if not isinstance(transferred, Mapping):
            raise ValueError(f"{self.model_id} processor output must be a mapping")
        with torch.inference_mode(), autocast_context(
            torch.device(self._execution_device), self._execution_precision
        ):
            outputs = model(**transferred)
        logits = getattr(outputs, "logits", None)
        if not isinstance(logits, torch.Tensor):
            raise ValueError(f"{self.model_id} model output must expose tensor logits")
        return logits

    def to_cpu(self, outputs: Any) -> tuple[Mapping[str, Any], ...]:
        labels = self._require_labels()
        watermark_index = self._require_watermark_index()
        if not isinstance(outputs, torch.Tensor):
            raise ValueError(f"{self.model_id} model output must be a tensor")
        logits = outputs.detach().to("cpu")
        if logits.ndim != 2:
            raise ValueError(f"{self.model_id} logits must be two-dimensional")
        if logits.shape[1] != len(labels):
            raise ValueError(f"{self.model_id} logits do not match the fixed label map")
        rows = torch.softmax(logits.float(), dim=-1).tolist()
        return tuple(
            {
                "raw_softmax_label_score": float(row[watermark_index]),
                "raw_softmax_label_scores": {
                    labels[index]: float(row[index]) for index in sorted(labels)
                },
            }
            for row in rows
        )

    def close(self) -> None:
        runtime = self._runtime
        self._runtime = None
        self._labels = None
        self._watermark_index = None
        self._execution_device = None
        self._execution_precision = None
        self._provenance = None
        _close_runtime_references(runtime)

    def _require_runtime(self) -> Any:
        if self._runtime is None:
            raise RuntimeError(f"{self.model_id} runtime is not loaded")
        return self._runtime

    def _require_labels(self) -> dict[int, str]:
        if self._labels is None:
            raise RuntimeError(f"{self.model_id} adapter is not loaded")
        return self._labels

    def _require_watermark_index(self) -> int:
        if self._watermark_index is None:
            raise RuntimeError(f"{self.model_id} adapter is not loaded")
        return self._watermark_index


class WD14TaggerBenchmarkAdapter:
    """Run the preflight-pinned WD14 tagger with its fixed local transform."""

    model_id = WD14_EVA02_CONTRACT.model_id

    def __init__(
        self,
        *,
        wd14_loader: Any | None = None,
        preprocess_image: Callable[[Image.Image], torch.Tensor] | None = None,
    ) -> None:
        self._wd14_loader = wd14_loader or WD14TaggerPreflightAdapter(WD14_EVA02_CONTRACT)
        self._preprocess_image = preprocess_image or preprocess_wd14_image
        self._model: Any | None = None
        self._tags: tuple[str, ...] | None = None
        self._tag_indices: dict[str, int] | None = None
        self._execution_device: str | None = None
        self._execution_precision: Precision | None = None
        self._provenance: BenchmarkSidecarDetectorProvenance | None = None

    @property
    def provenance(self) -> BenchmarkSidecarDetectorProvenance:
        if self._provenance is None:
            raise RuntimeError(f"{self.model_id} adapter is not loaded")
        return self._provenance

    def load(
        self,
        *,
        preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: Precision,
    ) -> None:
        _require_ready_matched_report(preflight_report, model_id=self.model_id)
        if self._model is not None:
            raise RuntimeError(f"{self.model_id} adapter is already loaded")
        if self._wd14_loader is None:
            raise RuntimeError(f"{self.model_id} adapter has been closed")
        try:
            loaded = self._wd14_loader.load(preflight_report)
            model = getattr(loaded, "model", None)
            if model is None:
                raise RuntimeError(f"{self.model_id} loader returned no model")
            tags = tuple(getattr(loaded, "tags", ()))
            if len(tags) != len(set(tags)) or not REQUIRED_WD14_TAGS.issubset(tags):
                raise RuntimeError(f"{self.model_id} loader returned invalid approved tags")
            self._model = model
            device = resolve_torch_device(
                cast(DeviceRequest, requested_device),
                requested_precision,
            )
            move = getattr(model, "to", None)
            if callable(move):
                moved = move(device)
                if moved is not None:
                    model = moved
            self._model = model
            self._tags = tags
            self._tag_indices = {tag: index for index, tag in enumerate(tags)}
            self._provenance = BenchmarkSidecarDetectorProvenance(
                model_id=self.model_id,
                assets=(_preflight_asset_to_sidecar(preflight_report, model_id=self.model_id),),
                preprocessing=BenchmarkSidecarPreprocessingSource(
                    source=WD14_EVA02_CONTRACT.source_repository,
                    revision=WD14_EVA02_CONTRACT.revision,
                ),
            )
        except BaseException:
            with suppress(BaseException):
                self.close()
            raise

    def preprocess(self, images: Sequence[Image.Image]) -> torch.Tensor:
        if self._preprocess_image is None:
            raise RuntimeError(f"{self.model_id} adapter is closed")
        self._require_model()
        return _stack_preprocessed_images(images, self._preprocess_image)

    def transfer_to_device(
        self,
        prepared: torch.Tensor,
        *,
        device: str,
        precision: Precision,
    ) -> torch.Tensor:
        self._require_model()
        self._execution_device = device
        self._execution_precision = precision
        return prepared.to(device)

    def forward(self, transferred: torch.Tensor) -> Any:
        model = self._require_model()
        if self._execution_device is None or self._execution_precision is None:
            raise RuntimeError(f"{self.model_id} inputs were not transferred to a device")
        with torch.inference_mode(), autocast_context(
            torch.device(self._execution_device), self._execution_precision
        ):
            return model(transferred)

    def to_cpu(self, outputs: Any) -> tuple[Mapping[str, Any], ...]:
        tag_indices = self._require_tag_indices()
        tags = self._require_tags()
        if not isinstance(outputs, torch.Tensor):
            raise ValueError(f"{self.model_id} tag output must be a tensor")
        logits = outputs.detach().to("cpu")
        if logits.ndim != 2:
            raise ValueError(f"{self.model_id} tag output must be two-dimensional")
        if logits.shape[1] != len(tags):
            raise ValueError(f"{self.model_id} tag output does not match the fixed tag table")
        rows = torch.sigmoid(logits).tolist()
        return tuple(
            {
                "raw_sigmoid_tag_scores": {
                    tag: float(row[tag_indices[tag]]) for tag in sorted(REQUIRED_WD14_TAGS)
                }
            }
            for row in rows
        )

    def close(self) -> None:
        model = self._model
        loader = self._wd14_loader
        self._model = None
        self._wd14_loader = None
        self._preprocess_image = None
        self._tags = None
        self._tag_indices = None
        self._execution_device = None
        self._execution_precision = None
        self._provenance = None
        _close_runtime_references(model, loader)

    def _require_model(self) -> Any:
        if self._model is None:
            raise RuntimeError(f"{self.model_id} model is not loaded")
        return self._model

    def _require_tags(self) -> tuple[str, ...]:
        if self._tags is None:
            raise RuntimeError(f"{self.model_id} adapter is not loaded")
        return self._tags

    def _require_tag_indices(self) -> dict[str, int]:
        if self._tag_indices is None:
            raise RuntimeError(f"{self.model_id} adapter is not loaded")
        return self._tag_indices


class PPOCRv5BenchmarkAdapter:
    """Run PP-OCRv5 only as independent raw region evidence."""

    model_id = OCR_DETECTOR_MODEL_ID

    def __init__(
        self,
        *,
        runtime_assets: RuntimeAssets,
        runtime_factory: Callable[[RuntimeAssets, str, Precision, int], Any] = (
            _default_ocr_runtime_factory
        ),
    ) -> None:
        self._runtime_assets = runtime_assets
        self._runtime_factory = runtime_factory
        self._runtime: Any | None = None
        self._execution_device: str | None = None
        self._execution_precision: Precision | None = None
        self._provenance: BenchmarkSidecarAuxiliaryOcrProvenance | None = None

    @property
    def provenance(self) -> BenchmarkSidecarAuxiliaryOcrProvenance:
        if self._provenance is None:
            raise RuntimeError("PP-OCRv5 adapter is not loaded")
        return self._provenance

    def load(
        self,
        *,
        detector_preflight_report: DetectorPreflightReport,
        recognizer_preflight_report: DetectorPreflightReport,
        requested_device: str,
        requested_precision: Precision,
        recognizer_batch_size: int = 1,
    ) -> None:
        _require_ready_matched_report(
            detector_preflight_report,
            model_id=OCR_DETECTOR_MODEL_ID,
        )
        _require_ready_matched_report(
            recognizer_preflight_report,
            model_id=OCR_RECOGNIZER_MODEL_ID,
        )
        if self._runtime is not None:
            raise RuntimeError("PP-OCRv5 adapter is already loaded")
        try:
            selected_assets = select_runtime_assets(
                self._runtime_assets,
                (OCR_DETECTOR_MODEL_ID, OCR_RECOGNIZER_MODEL_ID),
            )
        except KeyError as error:
            raise RuntimeError("PP-OCRv5 requires detector and recognizer RuntimeAssets") from error
        detector_asset = selected_assets.get(OCR_DETECTOR_MODEL_ID)
        recognizer_asset = selected_assets.get(OCR_RECOGNIZER_MODEL_ID)
        _require_runtime_asset_matches_preflight(detector_asset, detector_preflight_report)
        _require_runtime_asset_matches_preflight(recognizer_asset, recognizer_preflight_report)
        try:
            resolved_device = resolve_torch_device(
                cast(DeviceRequest, requested_device),
                requested_precision,
            )
            runtime = self._runtime_factory(
                selected_assets,
                str(resolved_device),
                requested_precision,
                recognizer_batch_size,
            )
            self._runtime = runtime
            if not callable(getattr(runtime, "score", None)):
                raise RuntimeError("PP-OCRv5 runtime must expose a callable score method")
            self._provenance = BenchmarkSidecarAuxiliaryOcrProvenance(
                assets=(
                    _runtime_asset_to_sidecar(detector_asset, role="model"),
                    _runtime_asset_to_sidecar(recognizer_asset, role="model"),
                ),
                preprocessing=BenchmarkSidecarPreprocessingSource(
                    source="dataset_audit_studio.components.ocr_evidence.runtime.OCREvidenceRuntime",
                    revision="local-runtime-assets",
                ),
            )
        except BaseException:
            with suppress(BaseException):
                self.close()
            raise

    def preprocess(self, images: Sequence[Image.Image]) -> tuple[Image.Image, ...]:
        self._require_runtime()
        if not images:
            raise ValueError("OCR adapter batch must not be empty")
        return tuple(image.convert("RGB") for image in images)

    def transfer_to_device(
        self,
        prepared: tuple[Image.Image, ...],
        *,
        device: str,
        precision: Precision,
    ) -> tuple[Image.Image, ...]:
        self._require_runtime()
        self._execution_device = device
        self._execution_precision = precision
        return prepared

    def forward(self, transferred: tuple[Image.Image, ...]) -> Sequence[Mapping[str, Any]]:
        runtime = self._require_runtime()
        if self._execution_device is None or self._execution_precision is None:
            raise RuntimeError("PP-OCRv5 inputs were not transferred to a device")
        score = getattr(runtime, "score", None)
        if not callable(score):
            raise RuntimeError("PP-OCRv5 runtime must expose a callable score method")
        outputs = score(transferred)
        if isinstance(outputs, (str, bytes)) or not isinstance(outputs, Sequence):
            raise ValueError("PP-OCRv5 score output must be a batch sequence")
        return outputs

    def to_cpu(self, outputs: Any) -> tuple[Mapping[str, Any], ...]:
        self._require_runtime()
        if isinstance(outputs, (str, bytes)) or not isinstance(outputs, Sequence):
            raise ValueError("PP-OCRv5 score output must be a batch sequence")
        if any(not isinstance(output, Mapping) for output in outputs):
            raise ValueError("PP-OCRv5 score output must contain region objects")
        return tuple(dict(output) for output in outputs)

    def close(self) -> None:
        runtime = self._runtime
        self._runtime = None
        self._execution_device = None
        self._execution_precision = None
        self._provenance = None
        _close_runtime_references(runtime)

    def _require_runtime(self) -> Any:
        if self._runtime is None:
            raise RuntimeError("PP-OCRv5 runtime is not loaded")
        return self._runtime


def _watermark_labels(runtime: Any) -> dict[int, str]:
    raw_labels = getattr(runtime, "labels", None)
    if not isinstance(raw_labels, Mapping):
        raise RuntimeError("Watermark model config must expose a label map")
    labels: dict[int, str] = {}
    for raw_index, raw_label in raw_labels.items():
        if (
            not isinstance(raw_index, int)
            or not isinstance(raw_label, str)
            or not raw_label.strip()
        ):
            raise RuntimeError("Watermark model config must expose integer nonempty labels")
        labels[raw_index] = raw_label
    if not labels or set(labels) != set(range(len(labels))):
        raise RuntimeError("Watermark model config must expose contiguous label indexes")
    return labels
