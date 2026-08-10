from __future__ import annotations

import pytest
import torch
from dataset_audit_studio.components.artist_style.lsnet_runtime import (
    _checkpoint_state_dict,
    _head_dimensions,
)


def test_lsnet_checkpoint_state_dict_normalizes_distributed_prefixes() -> None:
    state = {
        "module.head.l.weight": torch.zeros((17, 4)),
        "module.head.bn.weight": torch.zeros((2_048,)),
    }
    normalized = _checkpoint_state_dict({"model": state})
    assert set(normalized) == {"head.l.weight", "head.bn.weight"}
    assert _head_dimensions(normalized) == (17, 2_048)


def test_lsnet_checkpoint_rejects_non_tensor_payloads() -> None:
    with pytest.raises(RuntimeError, match="tensor state dictionary"):
        _checkpoint_state_dict({"model": {"head.weight": "not-a-tensor"}})
