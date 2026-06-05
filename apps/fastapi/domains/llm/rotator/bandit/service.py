"""Imperative Shell — Redis I/O, mode resolution, predict/update orchestration,
slot reservations, OTel.

Pure scoring + reward + context-vector logic lives in domain.py; CellState
data + invariant mutations live in entities.py. Activated default since
2026-05-23: FGTS-VA (NeurIPS 2025). Mode is per-call (predict()/predict_top_k()
accept a `mode=` override) so a shadow-A/B harness can route real traffic
under one mode while computing the counterfactual under another. See
docs/KD-ROTATOR-BANDIT-SOTA-2026-05-23.md.

Env vars (priority order):
    KD_BANDIT_MODE = ucb | ts | fgts_va    explicit selection
    KD_DISABLE_BANDIT_TS = 1                kill-switch → ucb (full revert)
    KD_DISABLE_FGTS_VA   = 1                kill-switch → ts  (revert one step)
    (none set)                              DEFAULT = fgts_va
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import redis.asyncio as redis_aio

from core.otel import get_meter

from .domain import Mode, score_cell
from .entities import CellState
from .keys import (
    CACHE_PREFIX,
    cell_key,
    provider_slot_key,
    reservation_key,
)
from .params import (
    CELL_TTL_S, 
    UCB_ALPHA
)


logger = logging.getLogger(__name__)


def _resolve_mode(override: Mode | None = None) -> Mode:
    """Resolution priority: kwarg → KD_BANDIT_MODE env → kill-switches → fgts_va."""
    if override is not None:
        return override
    if "KD_BANDIT_MODE" in os.environ:
        explicit = os.environ["KD_BANDIT_MODE"].strip().lower()
        if explicit in ("ucb", "ts", "fgts_va"):
            return explicit  # type: ignore[return-value]
    if "KD_DISABLE_BANDIT_TS" in os.environ and os.environ["KD_DISABLE_BANDIT_TS"] == "1":
        return "ucb"
    if "KD_DISABLE_FGTS_VA" in os.environ and os.environ["KD_DISABLE_FGTS_VA"] == "1":
        return "ts"
    return "fgts_va"


# Shared module-level RNG. numpy.Generator draws are self-contained and safe
# to call concurrently from asyncio coroutines.
_RNG = np.random.default_rng()


try:
    logger.info(f"[pareto] bandit scoring mode at startup: {_resolve_mode()}")
except Exception:
    pass


# --------------------------------------------------------------------------- #
# Redis persistence
# --------------------------------------------------------------------------- #
async def get_cell_state(
    deployment: str,
    dd_process: str,
    *,
    redis: "redis_aio.Redis | None",
) -> CellState | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(cell_key(deployment, dd_process))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return CellState.from_dict(json.loads(raw))
    except Exception as e:
        logger.debug(f"[pareto] cell read failed for {deployment}:{dd_process}: {e}")
        return None


async def save_cell_state(
    state: CellState,
    *,
    redis: "redis_aio.Redis | None",
) -> bool:
    if redis is None:
        return False
    try:
        await redis.set(
            cell_key(state.deployment, state.dd_process),
            json.dumps(state.to_dict()),
            ex = CELL_TTL_S,
        )
        return True
    except Exception as e:
        logger.debug(f"[pareto] cell write failed for {state.deployment}:{state.dd_process}: {e}")
        return False


async def get_all_cells(
    *,
    redis: "redis_aio.Redis | None",
    pattern: str | None = None,
) -> list[CellState]:
    if redis is None:
        return []
    out: list[CellState] = []
    scan_pattern = f"{CACHE_PREFIX}*{pattern}" if pattern else f"{CACHE_PREFIX}*"
    try:
        async for key in redis.scan_iter(match=scan_pattern):
            try:
                raw = await redis.get(key)
                if raw is None:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode()
                out.append(CellState.from_dict(json.loads(raw)))
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[pareto] cell scan failed: {type(e).__name__}: {e}")
    return out


async def init_bandit_warm_start(
    deployments_by_step: dict[str, list[tuple[str, float]]],
    *,
    redis: "redis_aio.Redis | None",
    overwrite: bool = False,
) -> int:
    """Initialize cells for all (deployment, dd_process) pairs from benchmark priors."""
    if redis is None or not deployments_by_step:
        return 0
    count = 0
    for dd_process, deployments in deployments_by_step.items():
        for deployment_id, score in deployments:
            if not overwrite:
                existing = await get_cell_state(
                    deployment_id, 
                    dd_process, 
                    redis = redis)
                if existing is not None:
                    continue
            cell = CellState.fresh(deployment_id, dd_process, score)
            if await save_cell_state(
                cell, 
                redis = redis):
                count += 1
    logger.info(
        f"[pareto] warm-start: initialized {count} cells across "
        f"{len(deployments_by_step)} dd_processes"
    )
    return count


# --------------------------------------------------------------------------- #
# Predict / update
# --------------------------------------------------------------------------- #
async def predict(
    dd_process: str,
    context: np.ndarray,
    candidate_deployments: list[str],
    *,
    redis: "redis_aio.Redis | None",
    alpha: float = UCB_ALPHA,
    mode: Mode | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Pick deployment via the configured scoring mode."""
    if not candidate_deployments:
        return None, {"reason": "no_candidates"}
    resolved_mode = _resolve_mode(mode)
    cells = await asyncio.gather(
        *[get_cell_state(d, dd_process, redis = redis) for d in candidate_deployments]
    )
    scored: list[tuple[str, float, float, float, int]] = []
    for deployment, cell in zip(candidate_deployments, cells):
        if cell is None:
            cell = CellState.fresh(deployment, dd_process, benchmark_prior = 0.0)
        total, exploit, explore = score_cell(
            cell, 
            context, 
            resolved_mode, 
            rng = _RNG, 
            alpha = alpha)
        scored.append((deployment, total, exploit, explore, cell.n_obs))
    # Tie-break by lowest n_obs → favors exploration of under-sampled arms.
    scored.sort(key = lambda x: (-x[1], x[4], x[0]))
    winner = scored[0]
    _record_predict(dd_process, resolved_mode)
    _record_score(winner[1], resolved_mode)
    debug = {
        "winner":               winner[0],
        "winner_score":         winner[1],
        "winner_exploit":       winner[2],
        "winner_explore_bonus": winner[3],
        "winner_n_obs":         winner[4],
        "mode":                 resolved_mode,
        # Retained for backward-compat with dashboards that key on `winner_ucb`.
        "winner_ucb":           winner[1],
        "all_scores": [
            {"deployment": d, "score": t, "exploit": e, "explore": x, "n_obs": n}
            for d, t, e, x, n in scored
        ],
    }
    return winner[0], debug


async def update(
    deployment: str,
    dd_process: str,
    context: np.ndarray,
    reward: float,
    *,
    redis: "redis_aio.Redis | None",
) -> bool:
    """Apply one observation. Posterior advance is mode-agnostic — flipping
    KD_BANDIT_MODE later picks up the same accumulated state."""
    if redis is None:
        return False
    cell = await get_cell_state(deployment, dd_process, redis = redis)
    if cell is None:
        cell = CellState.fresh(deployment, dd_process, benchmark_prior = 0.0)
    cell.apply_update(context, reward)
    ok = await save_cell_state(cell, redis = redis)
    if ok:
        outcome = "positive" if reward > 0.5 else ("neutral" if reward > 0 else "negative")
        _record_update(dd_process, outcome)
        _record_sigma_sq(cell.sigma_sq_ewma)
    return ok


async def predict_top_k(
    dd_process: str,
    context: np.ndarray,
    candidate_deployments: list[str],
    *,
    redis: "redis_aio.Redis | None",
    k: int = 3,
    alpha: float = UCB_ALPHA,
    mode: Mode | None = None,
) -> list[tuple[str, float, int]]:
    """Top-K ranking for cascade-aware routing (try #1 → #2 → #3 on failure)."""
    if not candidate_deployments:
        return []
    resolved_mode = _resolve_mode(mode)
    cells = await asyncio.gather(
        *[get_cell_state(d, dd_process, redis = redis) for d in candidate_deployments]
    )
    scored: list[tuple[str, float, int]] = []
    for deployment, cell in zip(candidate_deployments, cells):
        if cell is None:
            cell = CellState.fresh(deployment, dd_process, benchmark_prior = 0.0)
        total, _exploit, _bonus = score_cell(
            cell, 
            context, 
            resolved_mode, 
            rng = _RNG, 
            alpha = alpha)
        scored.append((deployment, total, cell.n_obs))
    scored.sort(key = lambda x: (-x[1], x[2], x[0]))
    _record_predict(dd_process, resolved_mode)
    if scored:
        _record_score(scored[0][1], resolved_mode)
    return scored[: max(1, k)]


# --------------------------------------------------------------------------- #
# Reservations — thundering-herd protection (cell-level + provider-level)
# --------------------------------------------------------------------------- #
async def try_reserve(
    deployment: str,
    dd_process: str,
    *,
    redis: "redis_aio.Redis | None",
    ttl_s: int = 60,
) -> bool:
    """Atomic claim of (deployment, dd_process). False ⇒ another caller holds
    it; pick a different arm. Fail-soft on Redis errors → True."""
    if redis is None:
        return True
    try:
        claimed = await redis.set(
            reservation_key(deployment, dd_process), 
            "1", 
            ex = ttl_s, 
            nx = True)
        return bool(claimed)
    except Exception:
        return True


async def release_reservation(
    deployment: str,
    dd_process: str,
    *,
    redis: "redis_aio.Redis | None",
) -> None:
    if redis is None:
        return
    try:
        await redis.delete(reservation_key(deployment, dd_process))
    except Exception:
        pass


async def try_reserve_provider_slot(
    provider: str,
    *,
    redis: "redis_aio.Redis | None",
    max_slots: int,
    ttl_s: int = 1800,
) -> int | None:
    """Claim one of `max_slots` numbered slots. Returns the slot index or
    None when all are taken. Fail-soft on Redis None → returns 0."""
    if redis is None:
        return 0
    for slot_idx in range(max_slots):
        try:
            claimed = await redis.set(
                provider_slot_key(provider, slot_idx), 
                "1", 
                ex = ttl_s, 
                nx = True)
            if claimed:
                return slot_idx
        except Exception:
            continue
    return None


async def release_provider_slot(
    provider: str,
    slot_idx: int | None,
    *,
    redis: "redis_aio.Redis | None",
) -> None:
    if redis is None or slot_idx is None:
        return
    try:
        await redis.delete(provider_slot_key(provider, slot_idx))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# OTel instruments
# --------------------------------------------------------------------------- #
_metric_instruments: dict[str, Any] = {}


def _ensure_metrics() -> dict[str, Any]:
    if _metric_instruments:
        return _metric_instruments
    try:
        meter = get_meter()
        if meter is None:
            return _metric_instruments
        _metric_instruments["predict_counter"] = meter.create_counter(
            name = "dd.pareto_predict_total",
            description = "Bandit predict() calls — labels: dd_process, mode ∈ {ucb, ts, fgts_va}",
        )
        _metric_instruments["update_counter"] = meter.create_counter(
            name = "dd.pareto_update_total",
            description = "Bandit update() calls — labels: dd_process, outcome ∈ {positive, neutral, negative}",
        )
        _metric_instruments["score_hist"] = meter.create_histogram(
            name = "dd.pareto_score",
            description = (
                "Score of the winning deployment per predict() call. Semantics "
                "depend on mode label: 'ucb' = upper confidence bound, "
                "'ts'/'fgts_va' = sampled posterior score."
            ),
            unit = "1",
        )
        _metric_instruments["sigma_sq_hist"] = meter.create_histogram(
            name = "dd.pareto_sigma_sq",
            description = "Per-arm EWMA noise variance estimate after each update().",
            unit = "1",
        )
        _metric_instruments["shadow_agreement"] = meter.create_counter(
            name = "dd.pareto_shadow_agreement_total",
            description = "Shadow-mode: predicted == actual deployment — labels: dd_process, agreement ∈ {yes, no}",
        )
        logger.info(f"[pareto] {len(_metric_instruments)} OTel instruments registered")
    except Exception as e:
        logger.warning(f"[pareto] OTel init failed: {type(e).__name__}: {e}")
    return _metric_instruments


def _record_predict(dd_process: str, mode: Mode = "ucb") -> None:
    c = _ensure_metrics().get("predict_counter")
    if c is None:
        return
    try:
        c.add(1, attributes = {"dd_process": dd_process, "mode": mode})
    except Exception:
        pass


def _record_update(dd_process: str, outcome: str) -> None:
    c = _ensure_metrics().get("update_counter")
    if c is None:
        return
    try:
        c.add(1, attributes = {"dd_process": dd_process, "outcome": outcome})
    except Exception:
        pass


def _record_score(score: float, mode: Mode = "ucb") -> None:
    h = _ensure_metrics().get("score_hist")
    if h is None:
        return
    try:
        h.record(score, attributes={"mode": mode})
    except Exception:
        pass


def _record_sigma_sq(sigma_sq: float) -> None:
    h = _ensure_metrics().get("sigma_sq_hist")
    if h is None:
        return
    try:
        h.record(float(sigma_sq))
    except Exception:
        pass


def _record_shadow_agreement(dd_process: str, agreement: bool) -> None:
    c = _ensure_metrics().get("shadow_agreement")
    if c is None:
        return
    try:
        c.add(1, attributes = {
            "dd_process": dd_process,
            "agreement": "yes" if agreement else "no",
        })
    except Exception:
        pass
