"""
ParetoBandit-style adaptive routing for the LLM rotator (Phase 2).

DESIGN (2026-05-14): LinUCB with geometric forgetting on sufficient statistics,
following arXiv:2604.00136 (Taberner-Miller, Mar 2026). Per-(deployment,
kd_process) cell state lives in Redis. Reward signal blends success rate,
latency relative to expected, and KD-specific hash-recall ratio.

Composes with the rest of the stack:

    services.discovery.list_all_alive_models()      → DiscoveryRecord per provider
                          ↓
    services.benchmarks.rank_for_step()              → benchmark prior per cell
                          ↓
    services.llm_chain (Phase 1 dynamic catalog)     → Router model_list per step
                          ↓
    services.pareto_bandit  (Phase 2 — THIS module)  → UCB ranking refines per-call selection
       ↓                                        ↑
       ↓                              reward update from OTel span events
       ↓
    LiteLLM CustomRoutingStrategy (Phase 2 Day 4)    → actual routing decision

Cell state (one per (deployment, kd_process)):
    A_a:   (CONTEXT_DIM × CONTEXT_DIM) regularized covariance matrix
    b_a:   (CONTEXT_DIM,)              accumulated reward × context
    n_obs:                              total observations
    benchmark_prior:                    warm-start composite score
    last_updated:                       unix ts for staleness checks

UCB selection (per ParetoBandit paper):
    θ̂_a = A_a^-1 · b_a
    score_a(ψ) = ψ^T · θ̂_a + α · √(ψ^T · A_a^-1 · ψ)
    pick argmax_a score_a, breaking ties by lowest n_obs (exploration tiebreak)

Posterior update on reward r given context ψ:
    A_a ← (1 - γ) · A_a + ψ · ψ^T
    b_a ← (1 - γ) · b_a + r · ψ
    n_obs += 1

The (1 - γ) factor implements **geometric forgetting** — old observations
decay exponentially. γ ≈ 0.01 means each update decays history by 1%; after
~100 updates per cell, ancient observations contribute ~37% (e^-1) of their
original weight. This is what makes ParetoBandit robust to non-stationary
behavior (provider quality regression, rate-limit drift, model EOL).

WARM-START via benchmark prior:
    score_init = benchmark composite from services.benchmarks.compute_composite_score()
    θ̂_a(0)    = score_init · 1_unit_vector
    A_a(0)    = (1/max(score_init, 0.1)) · I_d         ← stronger prior for higher scores
    b_a(0)    = A_a(0) · θ̂_a(0)

So Day 1 ranking == Phase 1 ranking. PILOT-equivalent. The bandit's value emerges
as observations accumulate.

CONTEXT VECTOR (16 dims, intentionally small for fast convergence):
    [0]     constant bias                                 = 1.0
    [1]     chapter_number_normalized                     log(n+1) / log(20)
    [2]     expected_hash_count_normalized                log(n+1) / log(500)
    [3]     has_thinking_budget                           0 / 1
    [4-6]   vault_size_bucket (small/medium/large)        one-hot
    [7-15]  kd_process one-hot                            one-hot over 9 kd_processes

Cache layout:
    kd:rotator:pareto:cell:{deployment_id}:{kd_process}   → JSON blob, 90d TTL

OTel metrics emitted:
    kd.pareto_predict_total{kd_process}              Counter — calls to predict()
    kd.pareto_update_total{kd_process, outcome}      Counter — updates by reward bucket
    kd.pareto_ucb_score                              Histogram — score distribution per pick
    kd.pareto_n_obs{deployment, kd_process}          Gauge — observations per cell
    kd.pareto_cell_age_seconds                       Histogram — staleness per cell
    kd.pareto_shadow_agreement{kd_process}           Counter — predicted == actual deployment

The 'shadow_agreement' metric drives the production go/no-go: flip
KD_USE_PARETO_BANDIT to "1" only after agreement > 60% over 1-2 weeks.

Public API (async; sync wrappers provided where needed):
    await init_bandit_warm_start(deployments, redis)         # called at lifespan
    await predict(kd_process, context, redis)                 # returns (deployment, debug)
    await update(deployment, kd_process, context, reward, redis)
    await get_cell_state(deployment, kd_process, redis)       # introspection
    await get_all_cells(redis)                                # admin endpoint backing
    make_context_vector(...)                                  # feature extraction helper
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import redis.asyncio as redis_aio

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================
CONTEXT_DIM = 25
CACHE_PREFIX = "kd:rotator:pareto:cell:"
CELL_TTL_S = 90 * 24 * 3600       # 90 days — long-lived; forgetting handles staleness

# Geometric forgetting rate per update (γ). 0.01 means old observations decay
# to 37% (e^-1) of their original weight after ~100 updates. Tunable knob.
FORGETTING_GAMMA = 0.01

# UCB exploration coefficient. Higher = more exploration. 0.5 is conservative;
# the original LinUCB paper uses 1.0; ParetoBandit recommends adaptive scaling.
UCB_ALPHA = 0.5

# Ridge regularization for A_a (also serves as a small prior on θ̂_a → 0).
RIDGE_LAMBDA = 1.0


# kd_processes we serve. Ordering matters for the one-hot encoding.
KD_PROCESSES: tuple[str, ...] = (
    "kd-all",
    "kd-synth",
    "kd-reduce-label",
    "kd-keylm",
    "kd-embed",
    "kd-plan",
    "kd-curator",
    "kd-grader",
    "kd-critic",
)
_KD_PROCESS_IDX = {p: i for i, p in enumerate(KD_PROCESSES)}

# Providers ordered for one-hot in the context vector. Must match
# services.discovery.PROVIDERS keys (enabled subset). Slots [19-24].
CONTEXT_PROVIDERS: tuple[str, ...] = (
    "groq", "nim", "cerebras", "mistral", "gemini", "zhipu",
)
_PROVIDER_IDX = {p: i for i, p in enumerate(CONTEXT_PROVIDERS)}

# Error-class → reward penalty. Used by compose_reward() when the call fails.
# 429 is "try later" (light penalty), 500 is "broken" (heavy), auth is fatal.
ERROR_CLASS_PENALTIES: dict[str, float] = {
    "rate_limit":      -0.10,   # 429 — provider overloaded right now
    "timeout":         -0.30,   # call exceeded deadline — deployment too slow
    "server_error":    -0.50,   # 5xx — model crashed or deployment broken
    "auth_error":      -0.80,   # 401/403 — config wrong, avoid this deployment
    "schema_invalid":  -0.40,   # produced output but failed Pydantic validation
    "content_filter":  -0.20,   # provider refused — less bandit-actionable
    "unknown":         -0.40,   # catch-all for unmapped exceptions
}


# =============================================================================
# CellState — per (deployment, kd_process) bandit cell
# =============================================================================
@dataclass
class CellState:
    """LinUCB state for one (deployment, kd_process) pair.

    Wire format: JSON-serializable via to_dict()/from_dict(). A_a and b_a
    are stored as nested float lists in Redis (small footprint: 16×16 + 16
    floats ≈ 2KB per cell × 12 deployments × 9 processes = ~216KB total).
    """
    deployment: str
    kd_process: str
    A_a: np.ndarray              # (CONTEXT_DIM, CONTEXT_DIM)
    b_a: np.ndarray              # (CONTEXT_DIM,)
    n_obs: int
    last_updated: float          # unix ts
    benchmark_prior: float       # composite score at warm-start time

    @classmethod
    def fresh(cls, deployment: str, kd_process: str, benchmark_prior: float) -> "CellState":
        """Build a fresh cell, warm-started from the benchmark composite.

        Higher benchmark_prior → tighter prior (smaller covariance, more
        confidence). Below ~0.1 the prior is treated as "unknown" and we
        regularize with RIDGE_LAMBDA only.
        """
        prior = max(0.0, min(1.0, float(benchmark_prior)))
        # Diagonal regularization. Strong prior (high score) ⇒ small A → high confidence.
        # Weak prior ⇒ large A → wide UCB exploration.
        confidence = max(0.1, prior)
        A_a = (RIDGE_LAMBDA / confidence) * np.eye(CONTEXT_DIM, dtype=np.float64)
        # θ̂_a(0) = prior · 1_vec / CONTEXT_DIM → spread the prior across dims
        theta_init = (prior / CONTEXT_DIM) * np.ones(CONTEXT_DIM, dtype=np.float64)
        b_a = A_a @ theta_init
        return cls(
            deployment=deployment,
            kd_process=kd_process,
            A_a=A_a,
            b_a=b_a,
            n_obs=0,
            last_updated=time.time(),
            benchmark_prior=prior,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment": self.deployment,
            "kd_process": self.kd_process,
            "A_a": self.A_a.tolist(),
            "b_a": self.b_a.tolist(),
            "n_obs": self.n_obs,
            "last_updated": self.last_updated,
            "benchmark_prior": self.benchmark_prior,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CellState":
        return cls(
            deployment=d["deployment"],
            kd_process=d["kd_process"],
            A_a=np.asarray(d["A_a"], dtype=np.float64),
            b_a=np.asarray(d["b_a"], dtype=np.float64),
            n_obs=int(d.get("n_obs", 0)),
            last_updated=float(d.get("last_updated", time.time())),
            benchmark_prior=float(d.get("benchmark_prior", 0.0)),
        )

    def theta_hat(self) -> np.ndarray:
        """θ̂_a = A_a^-1 · b_a (current point estimate of the linear reward params)."""
        return np.linalg.solve(self.A_a, self.b_a)

    def ucb_score(self, context: np.ndarray, alpha: float = UCB_ALPHA) -> tuple[float, float, float]:
        """Compute UCB score = exploit + α · √(explore).

        Returns (total, exploit, explore) so the caller can log all three.
        """
        try:
            theta = self.theta_hat()
        except np.linalg.LinAlgError:
            # Degenerate covariance — fall back to benchmark prior as scalar reward
            return (self.benchmark_prior, self.benchmark_prior, 0.0)
        exploit = float(context @ theta)
        # ψ^T · A^-1 · ψ — explicit solve avoids forming A^-1
        A_inv_psi = np.linalg.solve(self.A_a, context)
        explore = float(context @ A_inv_psi)
        if explore < 0:
            explore = 0.0          # numerical safety
        bonus = alpha * float(np.sqrt(explore))
        return (exploit + bonus, exploit, bonus)

    def apply_update(self, context: np.ndarray, reward: float,
                     *, gamma: float = FORGETTING_GAMMA) -> None:
        """Update A_a and b_a with one observation. Applies geometric forgetting first."""
        keep = 1.0 - gamma
        self.A_a = keep * self.A_a + np.outer(context, context)
        self.b_a = keep * self.b_a + reward * context
        self.n_obs += 1
        self.last_updated = time.time()


# =============================================================================
# Context vector construction
# =============================================================================
def make_context_vector(
    kd_process: str,
    *,
    chapter_number: int = 0,
    expected_hash_count: int = 0,
    has_thinking_budget: bool = False,
    vault_size: int = 0,
    time_now: float | None = None,
    recent_error_rates: dict[str, float] | None = None,
) -> np.ndarray:
    """Build a 25-dim context vector from request + temporal features.

    Layout (expanded 2026-05-14 to add temporal + load signals):
        [0]      constant bias                                  = 1.0
        [1]      chapter_number_normalized        log(n+1)/log(20)
        [2]      expected_hash_count_normalized   log(n+1)/log(500)
        [3]      has_thinking_budget              0/1
        [4-6]    vault_size_bucket (small/med/large)   one-hot
        [7-15]   kd_process one-hot               over 9 processes (CONTEXT_DIM=25)
        [16]     hour_of_day_sin                  sin(2π·hour/24)   diurnal cycle
        [17]     hour_of_day_cos                  cos(2π·hour/24)   orthogonal
        [18]     day_of_week_normalized           weekday/6
        [19-24]  recent_5min_error_rate_per_provider [groq, nim, cerebras, mistral, gemini, zhipu]

    sin/cos hour encoding teaches the bandit that 23:00 and 00:00 are adjacent
    (linear encoding would treat them as far apart). Per-provider recent
    error rates let the bandit learn "NIM is degrading right now" even if
    our pinned NIM deployment looks individually OK.
    """
    v = np.zeros(CONTEXT_DIM, dtype=np.float64)
    v[0] = 1.0
    v[1] = float(np.log1p(max(0, chapter_number)) / np.log(20.0))
    v[2] = float(np.log1p(max(0, expected_hash_count)) / np.log(500.0))
    v[3] = 1.0 if has_thinking_budget else 0.0
    # vault_size buckets — small <50, medium 50-200, large >200
    if vault_size <= 50:
        v[4] = 1.0
    elif vault_size <= 200:
        v[5] = 1.0
    else:
        v[6] = 1.0
    # kd_process one-hot
    idx = _KD_PROCESS_IDX.get(kd_process)
    if idx is not None:
        v[7 + idx] = 1.0
    # Temporal — sin/cos hour + day-of-week
    ts = time_now if time_now is not None else time.time()
    tm = time.gmtime(ts)
    hour_frac = (tm.tm_hour + tm.tm_min / 60.0) / 24.0
    v[16] = float(np.sin(2 * np.pi * hour_frac))
    v[17] = float(np.cos(2 * np.pi * hour_frac))
    v[18] = float(tm.tm_wday / 6.0)
    # Per-provider recent error rate. Default 0 if unknown (treat as healthy).
    if recent_error_rates:
        for provider, rate in recent_error_rates.items():
            slot = _PROVIDER_IDX.get(provider)
            if slot is not None:
                v[19 + slot] = float(max(0.0, min(1.0, rate)))
    return v


# =============================================================================
# Reward composition — call this from OTel/LiteLLM callback with raw observations
# =============================================================================
def compose_reward(
    *,
    success: bool,
    schema_valid: bool = False,
    latency_s: float | None = None,
    expected_latency_s: float | None = None,
    hash_recall: float | None = None,
    error_class: str | None = None,
) -> float:
    """Build a scalar reward in approximately [-0.8, +1.0] range.

    Multi-signal reward (Phase 2 enhancement #1, 2026-05-14):
        Success path components (sum ~1.0 when all present):
            w_success       = 0.30   binary
            w_schema_valid  = 0.25   binary
            w_latency       = 0.20   centered-at-0 when latency == expected
            w_hash_recall   = 0.25   continuous, KD-specific structured-output recall

        Failure path (success=False): the reward is fully driven by error_class:
            429 rate_limit     → -0.10   (transient; "try later" not "broken")
            timeout            → -0.30
            5xx server_error   → -0.50
            auth_error         → -0.80
            schema_invalid     → -0.40
            content_filter     → -0.20
            unknown            → -0.40

    This lets the bandit distinguish "this deployment is rate-limited right
    now" from "this deployment is structurally broken" — different reward
    magnitudes lead to different cooldown/exploration dynamics.
    """
    if not success:
        return float(ERROR_CLASS_PENALTIES.get(error_class or "unknown", -0.40))

    r = 0.0
    r += 0.30  # success itself
    if schema_valid:
        r += 0.25
    if latency_s is not None and expected_latency_s and expected_latency_s > 0:
        ratio = float(latency_s) / float(expected_latency_s)
        lat_signal = max(-2.0, min(2.0, 1.0 - ratio))    # [-2, 2]
        r += 0.20 * (lat_signal / 2.0)                    # [-0.20, +0.20]
    if hash_recall is not None:
        r += 0.25 * max(0.0, min(1.0, float(hash_recall)))
    return r


# =============================================================================
# Redis persistence
# =============================================================================
def _cell_key(deployment: str, kd_process: str) -> str:
    return f"{CACHE_PREFIX}{deployment}:{kd_process}"


async def get_cell_state(
    deployment: str,
    kd_process: str,
    *,
    redis: "redis_aio.Redis | None",
) -> CellState | None:
    """Read one cell from Redis. Returns None if absent / unreadable."""
    if redis is None:
        return None
    try:
        raw = await redis.get(_cell_key(deployment, kd_process))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return CellState.from_dict(json.loads(raw))
    except Exception as e:
        logger.debug(f"[pareto] cell read failed for {deployment}:{kd_process}: {e}")
        return None


async def save_cell_state(
    state: CellState,
    *,
    redis: "redis_aio.Redis | None",
) -> bool:
    """Persist one cell to Redis. Returns True on success."""
    if redis is None:
        return False
    try:
        await redis.set(
            _cell_key(state.deployment, state.kd_process),
            json.dumps(state.to_dict()),
            ex=CELL_TTL_S,
        )
        return True
    except Exception as e:
        logger.debug(f"[pareto] cell write failed for {state.deployment}:{state.kd_process}: {e}")
        return False


async def get_all_cells(
    *,
    redis: "redis_aio.Redis | None",
    pattern: str | None = None,
) -> list[CellState]:
    """Scan all cells in Redis. Optional pattern restricts the scan.

    Pattern format: pass a Redis glob like "*kd-synth" to match only kd-synth
    cells, or None for everything.
    """
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


# =============================================================================
# Warm-start — populate all (deployment, kd_process) cells from benchmark prior
# =============================================================================
async def init_bandit_warm_start(
    deployments_by_step: dict[str, list[tuple[str, float]]],
    *,
    redis: "redis_aio.Redis | None",
    overwrite: bool = False,
) -> int:
    """Initialize cells for all (deployment, kd_process) pairs.

    Args:
        deployments_by_step: {kd_process: [(deployment_id, benchmark_score), ...]}
            Caller produces this by calling
                services.llm_chain._all_entries_current()  (for kd-all),
                services.llm_chain._synth_entries_current() (for kd-synth), etc.
            and looking up benchmark scores via services.benchmarks.rank_for_step.
        redis: Redis client (None → no persistence, returns 0).
        overwrite: when True, replaces existing cells; when False, leaves them.

    Returns: number of cells created or refreshed.
    """
    if redis is None or not deployments_by_step:
        return 0
    count = 0
    for kd_process, deployments in deployments_by_step.items():
        for deployment_id, score in deployments:
            if not overwrite:
                existing = await get_cell_state(deployment_id, kd_process, redis=redis)
                if existing is not None:
                    continue
            cell = CellState.fresh(deployment_id, kd_process, score)
            if await save_cell_state(cell, redis=redis):
                count += 1
    logger.info(
        f"[pareto] warm-start: initialized {count} cells across "
        f"{len(deployments_by_step)} kd_processes"
    )
    return count


# =============================================================================
# Predict — pick the highest-UCB deployment for a given (kd_process, context)
# =============================================================================
async def predict(
    kd_process: str,
    context: np.ndarray,
    candidate_deployments: list[str],
    *,
    redis: "redis_aio.Redis | None",
    alpha: float = UCB_ALPHA,
) -> tuple[str | None, dict[str, Any]]:
    """Pick deployment by UCB. Returns (deployment_id, debug_info).

    Args:
        kd_process: the step being routed (must be in KD_PROCESSES).
        context: 16-dim context vector from make_context_vector(...).
        candidate_deployments: list of deployment_id strings to consider.
            Typically the litellm_params.model strings from Phase 1's
            _xxx_entries_current() for the matching step.
        redis: Redis client. Required for cell lookup; if None, returns the
            first candidate as a degenerate fallback.
        alpha: UCB exploration coefficient.

    Returns: (deployment_id, {"scores": [...], "winner_n_obs": int, ...})
        On no candidates or all-cells-missing, deployment_id is None.
    """
    if not candidate_deployments:
        return None, {"reason": "no_candidates"}

    # Look up cell state for each candidate (batched gather)
    cells = await asyncio.gather(
        *[get_cell_state(d, kd_process, redis=redis) for d in candidate_deployments]
    )

    scored: list[tuple[str, float, float, float, int]] = []
    for deployment, cell in zip(candidate_deployments, cells):
        if cell is None:
            # No state yet — synthesize a cold cell with low prior (encourages exploration)
            cell = CellState.fresh(deployment, kd_process, benchmark_prior=0.0)
        total, exploit, explore = cell.ucb_score(context, alpha=alpha)
        scored.append((deployment, total, exploit, explore, cell.n_obs))

    # argmax with tie-break by lowest n_obs (exploration of under-sampled arms)
    scored.sort(key=lambda x: (-x[1], x[4], x[0]))
    winner = scored[0]

    _record_predict(kd_process)
    _record_ucb_score(winner[1])

    debug = {
        "winner": winner[0],
        "winner_ucb": winner[1],
        "winner_exploit": winner[2],
        "winner_explore_bonus": winner[3],
        "winner_n_obs": winner[4],
        "all_scores": [
            {"deployment": d, "ucb": t, "exploit": e, "explore": x, "n_obs": n}
            for d, t, e, x, n in scored
        ],
    }
    return winner[0], debug


# =============================================================================
# Update — apply reward to one cell
# =============================================================================
async def update(
    deployment: str,
    kd_process: str,
    context: np.ndarray,
    reward: float,
    *,
    redis: "redis_aio.Redis | None",
) -> bool:
    """Apply one observation's reward to the (deployment, kd_process) cell.

    Read cell → apply geometric forgetting + add observation → write back.
    Returns True on success, False on Redis failure (observation lost).
    """
    if redis is None:
        return False
    cell = await get_cell_state(deployment, kd_process, redis=redis)
    if cell is None:
        # Cell missing — create fresh (no benchmark prior available here)
        cell = CellState.fresh(deployment, kd_process, benchmark_prior=0.0)
    cell.apply_update(context, reward)
    ok = await save_cell_state(cell, redis=redis)
    if ok:
        outcome = "positive" if reward > 0.5 else ("neutral" if reward > 0 else "negative")
        _record_update(kd_process, outcome)
    return ok


# =============================================================================
# Top-K prediction — for cascade-aware routing (Phase 2 enhancement #3)
# =============================================================================
async def predict_top_k(
    kd_process: str,
    context: np.ndarray,
    candidate_deployments: list[str],
    *,
    redis: "redis_aio.Redis | None",
    k: int = 3,
    alpha: float = UCB_ALPHA,
) -> list[tuple[str, float, int]]:
    """Return the top-K deployments ranked by UCB score.

    Used by helpers.py for cascade-aware routing: try #1 → on fail try #2 →
    on fail try #3 → fall back to Phase 1 simple-shuffle Router. Each
    attempt gets its own error-class-typed reward submission.

    Returns: [(deployment_id, ucb_score, n_obs), ...] sorted desc by score.
    Length <= min(k, len(candidates)). Empty when no candidates.
    """
    if not candidate_deployments:
        return []

    cells = await asyncio.gather(
        *[get_cell_state(d, kd_process, redis=redis) for d in candidate_deployments]
    )

    scored: list[tuple[str, float, int]] = []
    for deployment, cell in zip(candidate_deployments, cells):
        if cell is None:
            cell = CellState.fresh(deployment, kd_process, benchmark_prior=0.0)
        total, _exploit, _bonus = cell.ucb_score(context, alpha=alpha)
        scored.append((deployment, total, cell.n_obs))

    # Sort: highest UCB first, ties broken by lowest n_obs (favor exploration
    # of under-sampled arms), then by deployment string for determinism.
    scored.sort(key=lambda x: (-x[1], x[2], x[0]))

    _record_predict(kd_process)
    if scored:
        _record_ucb_score(scored[0][1])

    return scored[: max(1, k)]


# =============================================================================
# OTel metric instruments
# =============================================================================
_metric_instruments: dict[str, Any] = {}


def _ensure_metrics() -> dict[str, Any]:
    if _metric_instruments:
        return _metric_instruments
    try:
        from services.otel_setup import get_meter
        meter = get_meter()
        if meter is None:
            return _metric_instruments
        _metric_instruments["predict_counter"] = meter.create_counter(
            name="kd.pareto_predict_total",
            description="ParetoBandit predict() calls — labels: kd_process",
        )
        _metric_instruments["update_counter"] = meter.create_counter(
            name="kd.pareto_update_total",
            description="ParetoBandit update() calls — labels: kd_process, outcome ∈ {positive, neutral, negative}",
        )
        _metric_instruments["ucb_score_hist"] = meter.create_histogram(
            name="kd.pareto_ucb_score",
            description="UCB score of the winning deployment per predict() call",
            unit="1",
        )
        _metric_instruments["shadow_agreement"] = meter.create_counter(
            name="kd.pareto_shadow_agreement_total",
            description="Shadow-mode: predicted == actual deployment — labels: kd_process, agreement ∈ {yes, no}",
        )
        logger.info(f"[pareto] {len(_metric_instruments)} OTel instruments registered")
    except Exception as e:
        logger.warning(f"[pareto] OTel init failed: {type(e).__name__}: {e}")
    return _metric_instruments


def _record_predict(kd_process: str) -> None:
    inst = _ensure_metrics()
    c = inst.get("predict_counter")
    if c is None:
        return
    try:
        c.add(1, attributes={"kd_process": kd_process})
    except Exception:
        pass


def _record_update(kd_process: str, outcome: str) -> None:
    inst = _ensure_metrics()
    c = inst.get("update_counter")
    if c is None:
        return
    try:
        c.add(1, attributes={"kd_process": kd_process, "outcome": outcome})
    except Exception:
        pass


def _record_ucb_score(score: float) -> None:
    inst = _ensure_metrics()
    h = inst.get("ucb_score_hist")
    if h is None:
        return
    try:
        h.record(score)
    except Exception:
        pass


def _record_shadow_agreement(kd_process: str, agreement: bool) -> None:
    inst = _ensure_metrics()
    c = inst.get("shadow_agreement")
    if c is None:
        return
    try:
        c.add(1, attributes={
            "kd_process": kd_process,
            "agreement": "yes" if agreement else "no",
        })
    except Exception:
        pass
