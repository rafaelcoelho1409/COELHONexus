from __future__ import annotations

import time
from typing import Literal

import numpy as np

from .config import FGTS_VA, REWARDS
from .entities import CellState
from .keys import (
    _DD_PROCESS_IDX, 
    _PROVIDER_IDX
)
from .params import (
    CONTEXT_DIM,
    ERROR_CLASS_PENALTIES,
    TS_SCALE,
    UCB_ALPHA,
)


Mode = Literal["ucb", "ts", "fgts_va"]


def theta_hat(cell: CellState) -> np.ndarray:
    """θ̂_a = A_a^-1 · b_a (point estimate of the linear reward params)."""
    return np.linalg.solve(cell.A_a, cell.b_a)


def score_ucb(
    cell: CellState,
    context: np.ndarray,
    alpha: float = UCB_ALPHA,
) -> tuple[float, float, float]:
    """LinUCB (Li et al. ICML 2010). Returns (total, exploit, explore_bonus)."""
    try:
        theta = theta_hat(cell)
    except np.linalg.LinAlgError:
        return (cell.benchmark_prior, cell.benchmark_prior, 0.0)
    exploit = float(context @ theta)
    A_inv_psi = np.linalg.solve(cell.A_a, context)
    explore = float(context @ A_inv_psi)
    if explore < 0:
        explore = 0.0
    bonus = alpha * float(np.sqrt(explore))
    return (exploit + bonus, exploit, bonus)


def score_ts(
    cell: CellState,
    context: np.ndarray,
    *,
    rng: np.random.Generator,
    scale: float = TS_SCALE,
) -> tuple[float, float, float]:
    """LinTS (Agrawal & Goyal ICML 2013). `explore` channel returns the L2
    perturbation norm for OTel parity with the UCB bonus."""
    try:
        theta_mean = theta_hat(cell)
    except np.linalg.LinAlgError:
        return (cell.benchmark_prior, cell.benchmark_prior, 0.0)
    try:
        A_inv = np.linalg.inv(cell.A_a)
    except np.linalg.LinAlgError:
        return (float(context @ theta_mean), float(context @ theta_mean), 0.0)
    cov = (scale * scale) * A_inv
    cov = 0.5 * (cov + cov.T)
    try:
        theta_sampled = rng.multivariate_normal(theta_mean, cov, check_valid="ignore")
    except (np.linalg.LinAlgError, ValueError):
        theta_sampled = theta_mean
    score = float(context @ theta_sampled)
    perturbation = float(np.linalg.norm(theta_sampled - theta_mean))
    return (score, float(context @ theta_mean), perturbation)


def score_fgts_va(
    cell: CellState,
    context: np.ndarray,
    *,
    rng: np.random.Generator,
    sigma_min_sq: float = FGTS_VA.sigma_min_sq,
    feel_good_beta: float = FGTS_VA.feel_good_beta,
) -> tuple[float, float, float]:
    """FGTS-VA (NeurIPS 2025, arXiv:2511.02123). Per-arm σ̂² replaces fixed
    scale²; feel-good β·√(ψᵀA^-1ψ) adds optimism. Returns (total, exploit, fg_bonus)."""
    try:
        theta_mean = theta_hat(cell)
    except np.linalg.LinAlgError:
        return (cell.benchmark_prior, cell.benchmark_prior, 0.0)
    sigma_sq = max(float(sigma_min_sq), float(cell.sigma_sq_ewma))
    try:
        A_inv = np.linalg.inv(cell.A_a)
    except np.linalg.LinAlgError:
        return (float(context @ theta_mean), float(context @ theta_mean), 0.0)
    cov = sigma_sq * A_inv
    cov = 0.5 * (cov + cov.T)
    try:
        theta_sampled = rng.multivariate_normal(theta_mean, cov, check_valid="ignore")
    except (np.linalg.LinAlgError, ValueError):
        theta_sampled = theta_mean
    exploit = float(context @ theta_sampled)
    bonus = 0.0
    if feel_good_beta > 0.0:
        try:
            A_inv_psi = np.linalg.solve(cell.A_a, context)
            explore_raw = float(context @ A_inv_psi)
            if explore_raw < 0.0:
                explore_raw = 0.0
            bonus = float(feel_good_beta) * float(np.sqrt(explore_raw))
        except np.linalg.LinAlgError:
            bonus = 0.0
    return (exploit + bonus, exploit, bonus)


def score_cell(
    cell: CellState,
    context: np.ndarray,
    mode: Mode,
    *,
    rng: np.random.Generator,
    alpha: float = UCB_ALPHA,
) -> tuple[float, float, float]:
    """Dispatch the three scoring modes. Returns (total, exploit, explore)."""
    if mode == "fgts_va":
        return score_fgts_va(cell, context, rng = rng)
    if mode == "ts":
        return score_ts(cell, context, rng = rng)
    return score_ucb(cell, context, alpha = alpha)


def make_context_vector(
    dd_process: str,
    *,
    chapter_number: int = 0,
    expected_hash_count: int = 0,
    has_thinking_budget: bool = False,
    vault_size: int = 0,
    time_now: float | None = None,
    recent_error_rates: dict[str, float] | None = None,
) -> np.ndarray:
    """24-dim feature vector. sin/cos hour encoding teaches the bandit that
    23:00 and 00:00 are adjacent."""
    v = np.zeros(CONTEXT_DIM, dtype = np.float64)
    v[0] = 1.0
    v[1] = float(np.log1p(max(0, chapter_number)) / np.log(20.0))
    v[2] = float(np.log1p(max(0, expected_hash_count)) / np.log(500.0))
    v[3] = 1.0 if has_thinking_budget else 0.0
    if vault_size <= 50:
        v[4] = 1.0
    elif vault_size <= 200:
        v[5] = 1.0
    else:
        v[6] = 1.0
    idx = _DD_PROCESS_IDX.get(dd_process)
    if idx is not None:
        v[7 + idx] = 1.0
    ts = time_now if time_now is not None else time.time()
    tm = time.gmtime(ts)
    hour_frac = (tm.tm_hour + tm.tm_min / 60.0) / 24.0
    v[16] = float(np.sin(2 * np.pi * hour_frac))
    v[17] = float(np.cos(2 * np.pi * hour_frac))
    v[18] = float(tm.tm_wday / 6.0)
    if recent_error_rates:
        for provider, rate in recent_error_rates.items():
            slot = _PROVIDER_IDX.get(provider)
            if slot is not None:
                v[19 + slot] = float(max(0.0, min(1.0, rate)))
    return v


def compose_reward(
    *,
    success: bool,
    schema_valid: bool = False,
    latency_s: float | None = None,
    expected_latency_s: float | None = None,
    hash_recall: float | None = None,
    error_class: str | None = None,
) -> float:
    """Scalar reward roughly in [-0.8, +1.0]. Failure path is fully driven by
    error_class — penalty magnitudes distinguish "rate-limited now" from
    "structurally broken"."""
    if not success:
        return float(ERROR_CLASS_PENALTIES.get(error_class or "unknown", ERROR_CLASS_PENALTIES["unknown"]))
    r = REWARDS.success
    if schema_valid:
        r += REWARDS.schema_valid
    if latency_s is not None and expected_latency_s and expected_latency_s > 0:
        ratio = float(latency_s) / float(expected_latency_s)
        lat_signal = max(-2.0, min(2.0, 1.0 - ratio))
        r += REWARDS.latency * (lat_signal / 2.0)
    if hash_recall is not None:
        r += REWARDS.hash_recall * max(0.0, min(1.0, float(hash_recall)))
    return r
