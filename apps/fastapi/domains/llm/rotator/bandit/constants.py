from __future__ import annotations

CONTEXT_DIM = 24
CACHE_PREFIX = "dd:rotator:pareto:cell:"
CELL_TTL_S = 90 * 24 * 3600       # 90 days — long-lived; forgetting handles staleness
# Geometric forgetting rate per update (γ). 0.01 means old observations decay
# to 37% (e^-1) of their original weight after ~100 updates. Tunable knob.
FORGETTING_GAMMA = 0.01
# UCB exploration coefficient. Higher = more exploration. 0.5 is conservative;
# the original LinUCB paper uses 1.0; ParetoBandit recommends adaptive scaling.
UCB_ALPHA = 0.5
# Ridge regularization for A_a (also serves as a small prior on θ̂_a → 0).
RIDGE_LAMBDA = 1.0

# =============================================================================
# Phase 3a — Linear Thompson Sampling (LinTS) hyperparameters
# =============================================================================
# Per Agrawal & Goyal (ICML 2013, arXiv:1209.3352) the posterior over θ_a is
# N(A_a^-1 b_a, v²·A_a^-1) with v = R·√(24/ε·d·ln(1/δ)). In practice the scale
# is treated as a tunable. 0.5 mirrors UCB_ALPHA's conservative exploration
# regime so the two modes have comparable exploration appetite during shadow
# A/B testing. Bump toward 1.0 if shadow agreement is too high (TS is
# behaving identically to UCB → not actually exploring posterior).
TS_SCALE = 0.5

# =============================================================================
# Phase 3c — Variance-Aware Feel-Good Thompson Sampling hyperparameters
# =============================================================================
# Per arXiv:2511.02123 (NeurIPS 2025). Two augmentations on top of LinTS:
#
#   1. Variance-aware (VA): replace the global scale v² with a per-arm online
#      estimate σ̂²_a of the noise variance in the reward stream. Rate-limited
#      providers have intrinsically higher σ̂² than direct-API providers, so
#      a global v² mis-scales exploration on both ends.
#
#   2. Feel-Good (FG): add a small additive optimism bonus β·√(ψᵀA_a^-1ψ) to
#      the sampled score. This is the FGTS "feel-good" regularization that
#      tightens the regret bound versus vanilla LinTS — it nudges exploration
#      toward arms that *could* be good rather than waiting for the posterior
#      sample variance to drive that exploration.
#
# Variance estimate is maintained per cell via EWMA on squared predictive
# residuals (r_t - ψ_tᵀ·θ̂_a)². Matches our existing forgetting pattern (the
# residual stream is treated as non-stationary the same way the mean estimate
# is). On serialization, the variance EWMA lives in CellState.sigma_sq_ewma.
#
# Initial value 0.25 = (0.5)² — chosen to match the dynamic range of compose_
# reward's output (~[-0.8, +1.0]); intuitively "we expect rewards to vary by
# about half a unit around the mean before any data". After ~30 observations
# the EWMA has reached steady state.
FGTS_VA_SIGMA_INIT_SQ = 0.25
# Floor: never sample with effective variance below this, even after many
# low-noise observations. Keeps a baseline exploration regardless. 0.04 = 0.2²
# — about 20% of the reward range.
FGTS_VA_SIGMA_MIN_SQ = 0.04
# EWMA learning rate for the variance estimate. α=0.1 means each new
# observation contributes 10% to σ̂²_a; effective half-life ≈ 7 observations.
# Matches the bandit's responsiveness to non-stationarity.
FGTS_VA_VAR_ALPHA = 0.1
# Feel-Good bonus coefficient. 0.1 = small additive optimism (relative to
# the LinTS sampling variance, which is typically ~0.5 in early observations).
# Set to 0.0 for pure variance-aware LinTS without the feel-good term.
FGTS_FEEL_GOOD_BETA = 0.1
# dd_processes we serve. Ordering matters for the one-hot encoding.
# dd_processes we serve. Ordering matters for the one-hot encoding.
DD_PROCESSES: tuple[str, ...] = (
    "dd-all",
    "dd-synth",
    "dd-reduce-label",
    "dd-keylm",
    "dd-embed",
    "dd-plan",
    "dd-curator",
    "dd-grader",
    "dd-critic",
)
_DD_PROCESS_IDX = {p: i for i, p in enumerate(DD_PROCESSES)}
# Providers ordered for one-hot in the context vector. Must match
# domains.llm.discovery.PROVIDERS keys (enabled subset). Slots [19-23].
CONTEXT_PROVIDERS: tuple[str, ...] = (
    "groq", "nim", "cerebras", "mistral", "gemini",
)
_PROVIDER_IDX = {p: i for i, p in enumerate(CONTEXT_PROVIDERS)}
# Error-class → reward penalty. Used by compose_reward() when the call fails.
# 429 is "try later" (light penalty), 500 is "broken" (heavy), auth is fatal.
# Tuned 2026-05-14 (canary v8 evidence): bumped timeout -0.30 → -0.60.
# Concurrent section-cascade queries fire 8-14 picks before any reward lands,
# so a single negative observation needs to be strong enough to flip ranking
# AFTER landing — otherwise the same dead arm keeps getting selected for 5+
# more queries while feedback catches up. -0.60 is enough that one timeout
# moves a 0.85-prior arm below most competitors.
ERROR_CLASS_PENALTIES: dict[str, float] = {
    "rate_limit":      -0.10,   # 429 — provider overloaded right now
    "timeout":         -0.60,   # call exceeded deadline — was -0.30; bumped for faster avoidance
    "server_error":    -0.50,   # 5xx — model crashed or deployment broken
    "auth_error":      -0.80,   # 401/403 — config wrong, avoid this deployment
    "schema_invalid":  -0.40,   # produced output but failed Pydantic validation
    "content_filter":  -0.20,   # provider refused — less bandit-actionable
    "unknown":         -0.40,   # catch-all for unmapped exceptions
}


_metric_instruments: dict[str, Any] = {}