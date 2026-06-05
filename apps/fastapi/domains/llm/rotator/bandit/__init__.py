"""Adaptive contextual bandit routing for the LLM rotator.

Active default since 2026-05-23: FGTS-VA (NeurIPS 2025). LinUCB / LinTS remain
reachable via env kill-switches; all three share the same (A_a, b_a) state, so
flipping does not invalidate Redis. See docs/KD-ROTATOR-BANDIT-SOTA-2026-05-23.md.
"""
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
