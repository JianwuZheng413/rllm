from __future__ import annotations
from typing import Any, Dict, List

import torch
from torch import Tensor
from torch.nn import Module, Parameter

from rllm.types import ColType, NAMode, StatType
from .coltype_encoder import ColTypeEncoder


class LinearEncoder(ColTypeEncoder):
    r"""A linear function based Transform for numerical features. It applies
    linear layer :obj:`torch.nn.Linear(in_dim, out_dim)` on each raw numerical
    feature and concatenates the output embeddings. Note that the
    implementation does this for all numerical features in a batched manner.
    """

    supported_types = {ColType.NUMERICAL}

    def __init__(
        self,
        in_dim: int,
        out_dim: int | None = None,
        stats_list: List[Dict[StatType, Any]] | None = None,
        post_module: Module | None = None,
        activate: Module | None = None,
    ):
        super().__init__(out_dim, stats_list, post_module)
        self.in_dim = in_dim
        self.activate = activate

    def post_init(self):
        r"""This is the actual initialization function."""
        mean = torch.tensor([stats[StatType.MEAN] for stats in self.stats_list])
        self.register_buffer("mean", mean)
        std = torch.tensor([stats[StatType.STD] for stats in self.stats_list]) + 1e-6
        self.register_buffer("std", std)
        num_cols = len(self.stats_list)
        self.weight = Parameter(torch.empty(num_cols, self.in_dim, self.out_dim))
        self.bias = Parameter(torch.empty(num_cols, self.out_dim))
        self.reset_parameters()

    def reset_parameters(self) -> None:
        super().reset_parameters()
        torch.nn.init.normal_(self.weight, std=0.01)
        torch.nn.init.zeros_(self.bias)

    def encode_forward(
        self,
        feat: Tensor,
    ) -> Tensor:
        if feat.ndim == 2:
            # feat: [batch_size, num_cols]
            feat = ((feat - self.mean) / self.std).unsqueeze(-1)
        elif feat.ndim == 3:
            # feat: [batch_size, num_cols, 1]
            feat = (feat - self.mean.unsqueeze(-1)) / self.std.unsqueeze(-1)
        # [batch_size, num_cols], [dim, num_cols]
        # -> [batch_size, num_cols, dim]
        x_lin = torch.einsum("ijk,jkl->ijl", feat, self.weight) / self.in_dim
        # [batch_size, num_cols, dim] + [num_cols, dim]
        # -> [batch_size, num_cols, dim]
        x = x_lin + self.bias

        if self.activate is not None:
            x = self.activate(x)
        return x
