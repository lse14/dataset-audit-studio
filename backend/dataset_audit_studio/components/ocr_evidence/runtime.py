from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
import transformers
from PIL import Image
from torch import nn

from dataset_audit_studio.components.ocr_evidence.config import OCREvidenceConfig
from dataset_audit_studio.core.model_assets import (
    RuntimeAssets,
    verify_runtime_asset_snapshot,
)
from dataset_audit_studio.core.torch_runtime import (
    autocast_context,
    release_torch_memory,
    resolve_torch_device,
)

OCR_DET_MODEL_ID = "ppocrv5_server_det"
OCR_REC_MODEL_ID = "ppocrv5_server_rec"


class OCREvidenceRuntime:
    def __init__(self, config: OCREvidenceConfig, assets: RuntimeAssets) -> None:
        verify_runtime_asset_snapshot(assets)
        self.config = config
        self.device = resolve_torch_device(config.device, config.precision)
        det_root = Path(assets.get(OCR_DET_MODEL_ID).root)
        rec_root = Path(assets.get(OCR_REC_MODEL_ID).root)
        object_detection = getattr(transformers, "AutoModelForObjectDetection", None)
        text_recognition = getattr(transformers, "AutoModelForTextRecognition", None)
        if object_detection is None or text_recognition is None:
            raise RuntimeError("Installed Transformers does not support PP-OCRv5")
        self.det_processor = transformers.AutoImageProcessor.from_pretrained(
            det_root,
            local_files_only=True,
            trust_remote_code=False,
        )
        self.det_model: nn.Module | None = object_detection.from_pretrained(
            det_root,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        ).eval().requires_grad_(False).to(self.device)
        self.rec_processor = transformers.AutoImageProcessor.from_pretrained(
            rec_root,
            local_files_only=True,
            trust_remote_code=False,
        )
        self.rec_model: nn.Module | None = text_recognition.from_pretrained(
            rec_root,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        ).eval().requires_grad_(False).to(self.device)

    def score(self, images: tuple[Image.Image, ...]) -> list[dict[str, Any]]:
        if self.det_processor is None or self.det_model is None:
            raise RuntimeError("OCR detection runtime was not initialized")
        rgb_images = [image.convert("RGB") for image in images]
        detections = self._detect(rgb_images)
        results: list[dict[str, Any]] = []
        crops: list[Image.Image] = []
        crop_targets: list[tuple[int, int]] = []
        for image_index, (image, detection) in enumerate(
            zip(rgb_images, detections, strict=True)
        ):
            boxes = detection["boxes"].detach().cpu().numpy()
            scores = detection["scores"].detach().cpu().tolist()
            regions: list[dict[str, Any]] = []
            area = 0.0
            for box, score in zip(boxes, scores, strict=True):
                points = np.asarray(box, dtype=np.float32).reshape(4, 2)
                area += abs(float(cv2.contourArea(points)))
                regions.append(
                    {
                        "box": points.tolist(),
                        "detection_score": float(score),
                        "text": "",
                        "recognition_score": 0.0,
                    }
                )
                crop = self._perspective_crop(image, points)
                if crop is not None:
                    crops.append(crop)
                    crop_targets.append((image_index, len(regions) - 1))
            results.append(
                {
                    "regions": regions,
                    "text_area_ratio": min(1.0, area / max(1, image.width * image.height)),
                }
            )
        try:
            self._recognize(crops, crop_targets, results)
        finally:
            for crop in crops:
                crop.close()
        return results

    def _detect(self, images: list[Image.Image]) -> list[dict[str, Any]]:
        processed = self.det_processor(images=images, return_tensors=None)
        pixel_values = list(processed["pixel_values"])
        target_sizes = list(processed["target_sizes"])
        if len(pixel_values) != len(images) or len(target_sizes) != len(images):
            raise RuntimeError("OCR detection processor returned an incomplete batch")
        groups: dict[tuple[int, ...], list[int]] = {}
        for index, value in enumerate(pixel_values):
            groups.setdefault(tuple(value.shape), []).append(index)
        detections: list[dict[str, Any] | None] = [None] * len(images)
        for indices in groups.values():
            batch = torch.stack([pixel_values[index] for index in indices]).to(self.device)
            targets = torch.stack(
                [torch.as_tensor(target_sizes[index]) for index in indices]
            )
            with torch.inference_mode(), autocast_context(self.device, self.config.precision):
                outputs = self.det_model(pixel_values=batch)
            group_detections = self.det_processor.post_process_object_detection(
                outputs,
                threshold=self.config.bitmap_threshold,
                target_sizes=targets,
                box_threshold=self.config.box_threshold,
                max_candidates=self.config.max_candidates,
                min_size=self.config.min_size,
                unclip_ratio=self.config.unclip_ratio,
            )
            if len(group_detections) != len(indices):
                raise RuntimeError("OCR detection postprocessor returned an incomplete batch")
            for index, detection in zip(indices, group_detections, strict=True):
                detections[index] = detection
        if any(detection is None for detection in detections):
            raise RuntimeError("OCR detection output order is incomplete")
        return [detection for detection in detections if detection is not None]

    def _recognize(
        self,
        crops: list[Image.Image],
        targets: list[tuple[int, int]],
        results: list[dict[str, Any]],
    ) -> None:
        if not crops:
            return
        if self.rec_processor is None or self.rec_model is None:
            raise RuntimeError("OCR recognition runtime was not initialized")
        batch_size = self.config.recognition_batch_size
        for start in range(0, len(crops), batch_size):
            end = min(start + batch_size, len(crops))
            inputs = self.rec_processor(images=crops[start:end], return_tensors="pt").to(
                self.device
            )
            with torch.inference_mode(), autocast_context(self.device, self.config.precision):
                outputs = self.rec_model(pixel_values=inputs["pixel_values"])
            recognized = self.rec_processor.post_process_text_recognition(outputs)
            for target, value in zip(targets[start:end], recognized, strict=True):
                image_index, region_index = target
                region = results[image_index]["regions"][region_index]
                recognition_score = float(value.get("score", 0.0))
                region["text"] = str(value.get("text", ""))
                region["recognition_score"] = recognition_score
                if not math.isfinite(recognition_score):
                    region["text"] = ""
                    region["recognition_score"] = 0.0

    @staticmethod
    def _perspective_crop(image: Image.Image, points: np.ndarray) -> Image.Image | None:
        width = max(
            np.linalg.norm(points[0] - points[1]),
            np.linalg.norm(points[2] - points[3]),
        )
        height = max(
            np.linalg.norm(points[0] - points[3]),
            np.linalg.norm(points[1] - points[2]),
        )
        target_width = int(round(width))
        target_height = int(round(height))
        if target_width < 1 or target_height < 1:
            return None
        destination = np.asarray(
            (
                (0, 0),
                (target_width - 1, 0),
                (target_width - 1, target_height - 1),
                (0, target_height - 1),
            ),
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(points.astype(np.float32), destination)
        crop = cv2.warpPerspective(
            np.asarray(image),
            matrix,
            (target_width, target_height),
            borderMode=cv2.BORDER_REPLICATE,
        )
        if target_height / max(1, target_width) >= 1.5:
            crop = np.rot90(crop)
        return Image.fromarray(np.ascontiguousarray(crop), mode="RGB")

    def close(self) -> None:
        self.det_processor = None
        self.det_model = None
        self.rec_processor = None
        self.rec_model = None
        release_torch_memory()
