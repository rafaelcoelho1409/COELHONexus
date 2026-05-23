"""Adaptive contextual bandit routing for the LLM rotator.

Active default (2026-05-23): FGTS-VA (Feel-Good Thompson Sampling with per-
arm Variance Awareness; NeurIPS 2025, arXiv:2511.02123). Two earlier modes —
LinUCB (ICML 2010) and LinTS (ICML 2013) — remain reachable via the kill-
switch ladder (`KD_DISABLE_FGTS_VA=1` → LinTS, `KD_DISABLE_BANDIT_TS=1` →
LinUCB) or via explicit `KD_BANDIT_MODE` env. All three modes share the
same `(A_a, b_a)` sufficient statistics, so flipping does not invalidate
Redis state.

Source-of-truth doc: docs/KD-ROTATOR-BANDIT-SOTA-2026-05-23.md
"""
from .service import (
    compose_reward,
    get_all_cells,
    get_cell_state,
    init_bandit_warm_start,
    make_context_vector,
    predict,
    predict_top_k,
    release_provider_slot,
    release_reservation,
    save_cell_state,
    try_reserve,
    try_reserve_provider_slot,
    update,
)
from .types import CellState

__all__ = [
    "make_context_vector",
    "compose_reward",
    "get_cell_state",
    "save_cell_state",
    "get_all_cells",
    "init_bandit_warm_start",
    "predict",
    "update",
    "predict_top_k",
    "try_reserve",
    "release_reservation",
    "try_reserve_provider_slot",
    "release_provider_slot",
    "CellState",
]
