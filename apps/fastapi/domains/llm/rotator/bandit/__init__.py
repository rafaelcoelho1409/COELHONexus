"""Adaptive contextual bandit routing (FGTS-VA NeurIPS 2025 default; LinUCB/LinTS via env kill-switches share the same (A_a, b_a) state)."""
from __future__ import annotations

from .domain import compose_reward, make_context_vector
from .entities import CellState
from .service import (
    get_all_cells,
    get_cell_state,
    init_bandit_warm_start,
    predict,
    predict_top_k,
    release_provider_slot,
    release_reservation,
    save_cell_state,
    try_reserve,
    try_reserve_provider_slot,
    update,
)


__all__ = [
    "CellState",
    "compose_reward",
    "get_all_cells",
    "get_cell_state",
    "init_bandit_warm_start",
    "make_context_vector",
    "predict",
    "predict_top_k",
    "release_provider_slot",
    "release_reservation",
    "save_cell_state",
    "try_reserve",
    "try_reserve_provider_slot",
    "update",
]
