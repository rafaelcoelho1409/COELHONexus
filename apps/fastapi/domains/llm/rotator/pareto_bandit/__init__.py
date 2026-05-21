"""ParetoBandit adaptive routing (LinUCB with geometric forgetting)."""
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
