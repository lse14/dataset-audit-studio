from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import torch
from dataset_audit_studio.components.aesthetic_domain import jtp3 as jtp3_module
from dataset_audit_studio.components.aesthetic_domain.fusion import FusionHead
from dataset_audit_studio.components.aesthetic_domain.jtp3 import (
    image_size_for_sequence,
    patchify_jtp3_images,
)
from dataset_audit_studio.components.clip_features.runtime import (
    build_ufd_transform,
    load_pinned_clip_state_dict,
    suppress_pinned_clip_bootstrap_warning,
)
from dataset_audit_studio.components.ocr_evidence.config import OCREvidenceConfig
from dataset_audit_studio.components.ocr_evidence.runtime import OCREvidenceRuntime
from dataset_audit_studio.core.torch_runtime import copy_float_features_to_tensor
from PIL import Image


def test_jtp3_resize_matches_fixed_reference_fixtures() -> None:
    fixtures = {
        (512, 512): (512, 512),
        (768, 1024): (432, 576),
        (2048, 512): (1024, 256),
        (1216, 1216): (512, 512),
        (1536, 864): (672, 384),
        (63, 4096): (48, 4096),
    }
    assert {size: image_size_for_sequence(*size) for size in fixtures} == fixtures


def test_jtp3_patch_order_coordinates_and_normalization() -> None:
    pixels = np.zeros((16, 16, 3), dtype=np.uint8)
    pixels[..., 0] = 255
    pixels[..., 1] = 127
    image = Image.fromarray(pixels, mode="RGB")
    patches, coordinates, valid = patchify_jtp3_images((image,), max_sequence=64)
    assert patches.shape == (1, 64, 768)
    assert coordinates[0, 0].tolist() == [0, 0]
    assert valid[0].tolist() == [True] + [False] * 63
    assert torch.isclose(patches[0, 0, 0], torch.tensor(1.0))
    assert torch.isclose(patches[0, 0, 1], torch.tensor(127 / 127.5 - 1.0))
    assert torch.isclose(patches[0, 0, 2], torch.tensor(-1.0))
    image.close()


def test_ufd_transform_center_crops_without_resize_and_uses_clip_normalization() -> None:
    pixels = np.zeros((260, 300, 3), dtype=np.uint8)
    pixels[..., 0] = np.arange(300, dtype=np.uint16) % 256
    pixels[..., 1] = np.arange(260, dtype=np.uint16)[:, None] % 256
    pixels[..., 2] = 64
    image = Image.fromarray(pixels, mode="RGB")
    tensor = build_ufd_transform()(image)
    assert tensor.shape == (3, 224, 224)
    expected_red = ((38 / 255.0) - 0.48145466) / 0.26862954
    expected_green = ((18 / 255.0) - 0.4578275) / 0.26130258
    expected_blue = ((64 / 255.0) - 0.40821073) / 0.27577711
    assert torch.allclose(
        tensor[:, 0, 0],
        torch.tensor((expected_red, expected_green, expected_blue)),
        atol=1e-6,
        rtol=0,
    )
    image.close()


def test_fusion_head_applies_reference_score_range_and_domain_logit() -> None:
    head = FusionHead(
        input_dim=2,
        hidden_dims=(),
        dropout=0.0,
        regression_heads=("aesthetic",),
        has_domain_head=True,
    ).eval()
    with torch.no_grad():
        head.reg_heads["aesthetic"].weight.copy_(torch.tensor([[1.0, 0.0]]))
        head.reg_heads["aesthetic"].bias.zero_()
        assert head.cls_head is not None
        head.cls_head.weight.copy_(torch.tensor([[0.0, 1.0]]))
        head.cls_head.bias.zero_()
        aesthetic, domain_logit = head(torch.tensor([[1.0, 2.0]]))
    assert torch.allclose(aesthetic, torch.sigmoid(torch.tensor([1.0])) * 4.0 + 1.0)
    assert domain_logit is not None
    assert torch.allclose(torch.sigmoid(domain_logit), torch.sigmoid(torch.tensor([2.0])))


def test_pinned_clip_torchscript_loads_without_pickle_fallback(tmp_path) -> None:
    class TinyArchive(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.weight = torch.nn.Parameter(torch.tensor([2.0]))
            self.register_buffer("input_resolution", torch.tensor(224))
            self.register_buffer("context_length", torch.tensor(77))
            self.register_buffer("vocab_size", torch.tensor(49_408))

        def forward(self, value: torch.Tensor) -> torch.Tensor:
            return value * self.weight

    archive = tmp_path / "clip.pt"
    torch.jit.trace(TinyArchive(), torch.ones(1)).save(str(archive))
    state_dict = load_pinned_clip_state_dict(archive)
    assert set(state_dict) == {"weight"}
    assert torch.equal(state_dict["weight"], torch.tensor([2.0]))


def test_pinned_clip_bootstrap_suppresses_only_its_expected_warning(caplog) -> None:
    caplog.set_level(logging.WARNING)
    with suppress_pinned_clip_bootstrap_warning():
        logging.warning(
            "No pretrained weights loaded for model 'ViT-L-14'. Model initialized randomly."
        )
        logging.warning("another runtime warning")

    assert [record.message for record in caplog.records] == ["another runtime warning"]


def test_copy_float_features_to_tensor_does_not_alias_immutable_array() -> None:
    features = np.arange(6, dtype=np.float32).reshape(2, 3)
    features.setflags(write=False)

    tensor = copy_float_features_to_tensor(features, device=torch.device("cpu"))
    tensor.add_(1)

    assert tensor.tolist() == [[1, 2, 3], [4, 5, 6]]
    assert features.tolist() == [[0, 1, 2], [3, 4, 5]]


def test_jtp3_loader_uses_safe_open_keys_api(monkeypatch, tmp_path) -> None:
    labels = "\n".join(f"label-{index}" for index in range(jtp3_module.JTP3_CLASS_COUNT))

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def metadata(self):
            return {
                "modelspec.architecture": jtp3_module.JTP3_ARCHITECTURE,
                "classifier.labels": labels,
            }

        def keys(self):
            return ("weight",)

        def get_tensor(self, key):
            assert key == "weight"
            return torch.tensor([1.0], dtype=torch.float16)

    captured = {}

    class TinyModel:
        def __init__(self, classes):
            assert classes == jtp3_module.JTP3_CLASS_COUNT

        def load_state_dict(self, state, *, strict):
            captured.update(state)
            assert strict is True

        def eval(self):
            return self

        def requires_grad_(self, _value):
            return self

        def to(self, **_kwargs):
            return self

    monkeypatch.setattr(jtp3_module, "safe_open", lambda *_args, **_kwargs: Reader())
    monkeypatch.setattr(jtp3_module, "JTP3Model", TinyModel)
    loaded = jtp3_module.load_jtp3_model(
        tmp_path / "model.safetensors",
        device=torch.device("cpu"),
    )
    assert isinstance(loaded, TinyModel)
    assert torch.equal(captured["weight"], torch.tensor([1.0]))


def test_ocr_detection_groups_heterogeneous_preprocessed_shapes() -> None:
    class Processor:
        def __call__(self, *, images, return_tensors):
            assert len(images) == 3
            assert return_tensors is None
            return {
                "pixel_values": [
                    torch.zeros((3, 8, 8)),
                    torch.zeros((3, 8, 12)),
                    torch.zeros((3, 8, 8)),
                ],
                "target_sizes": [
                    torch.tensor([40.0, 40.0]),
                    torch.tensor([40.0, 60.0]),
                    torch.tensor([40.0, 40.0]),
                ],
            }

        def post_process_object_detection(self, _outputs, *, target_sizes, **_kwargs):
            return [
                {
                    "boxes": torch.empty((0, 4, 2)),
                    "scores": torch.empty((0,)),
                }
                for _ in target_sizes
            ]

    class Model:
        def __init__(self) -> None:
            self.shapes = []

        def __call__(self, *, pixel_values):
            self.shapes.append(tuple(pixel_values.shape))
            return SimpleNamespace(last_hidden_state=torch.empty(0))

    runtime = object.__new__(OCREvidenceRuntime)
    model = Model()
    runtime.det_processor = Processor()
    runtime.det_model = model
    runtime.rec_processor = None
    runtime.rec_model = None
    runtime.device = torch.device("cpu")
    runtime.config = OCREvidenceConfig()
    images = [Image.new("RGB", (40, 40)) for _ in range(3)]
    try:
        results = runtime.score(tuple(images))
    finally:
        for image in images:
            image.close()
    assert model.shapes == [(2, 3, 8, 8), (1, 3, 8, 12)]
    assert [result["regions"] for result in results] == [[], [], []]
