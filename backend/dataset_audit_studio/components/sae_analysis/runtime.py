from __future__ import annotations

import random
from collections.abc import Callable

import numpy as np
import torch
import torch.nn.functional as functional
from torch import Tensor, nn

from dataset_audit_studio.components.sae_analysis.config import SparseAutoencoderConfig
from dataset_audit_studio.components.sae_analysis.contracts import SAEAnalysis


class SAEInterrupted(RuntimeError):
    pass


class SparseAutoencoder(nn.Module):
    def __init__(self, input_dim: int, feature_count: int) -> None:
        super().__init__()
        self.encoder = nn.Linear(input_dim, feature_count)
        self.decoder = nn.Linear(feature_count, input_dim)

    def encode(self, values: Tensor) -> Tensor:
        return functional.relu(self.encoder(values))

    def forward(self, values: Tensor) -> tuple[Tensor, Tensor]:
        activations = self.encode(values)
        return self.decoder(activations), activations


def train_sparse_autoencoder(
    embeddings: np.ndarray,
    config: SparseAutoencoderConfig,
    *,
    device: str = "cpu",
    should_stop: Callable[[], bool] | None = None,
) -> SAEAnalysis:
    matrix = np.asarray(embeddings, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise ValueError("SAE requires a non-empty embedding matrix")
    if np.any(~np.isfinite(matrix)):
        raise ValueError("SAE embeddings contain non-finite values")
    torch.manual_seed(config.seed)
    random.seed(config.seed)
    np.random.seed(config.seed)
    target_device = torch.device(device)
    values = torch.from_numpy(matrix)
    model = SparseAutoencoder(matrix.shape[1], config.feature_count).to(target_device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    generator = torch.Generator().manual_seed(config.seed)
    losses: list[float] = []
    for _ in range(config.epochs):
        permutation = torch.randperm(len(values), generator=generator)
        total = 0.0
        batches = 0
        for start in range(0, len(values), config.batch_size):
            if should_stop is not None and should_stop():
                raise SAEInterrupted("SAE control requested")
            batch_indices = permutation[start : start + config.batch_size]
            batch = values[batch_indices].to(target_device)
            reconstruction, activations = model(batch)
            loss = functional.mse_loss(reconstruction, batch)
            loss = loss + config.l1_coefficient * activations.abs().mean()
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            total += float(loss.detach().cpu())
            batches += 1
        losses.append(total / max(batches, 1))
    if should_stop is not None and should_stop():
        raise SAEInterrupted("SAE control requested")
    with torch.inference_mode():
        activations = model.encode(values.to(target_device)).cpu().numpy()
    thresholds = np.percentile(
        activations,
        config.activation_percentile,
        axis=0,
    ).astype(np.float32)
    top_count = min(config.top_k, len(values))
    top_indices = tuple(
        tuple(
            int(index)
            for index in np.argsort(-activations[:, feature], kind="stable")[:top_count]
        )
        for feature in range(config.feature_count)
    )
    state = {
        key: value.detach().cpu().contiguous() for key, value in model.state_dict().items()
    }
    return SAEAnalysis(
        state_dict=state,
        activations=activations.astype(np.float32),
        thresholds=thresholds,
        top_indices=top_indices,
        losses=tuple(losses),
    )
