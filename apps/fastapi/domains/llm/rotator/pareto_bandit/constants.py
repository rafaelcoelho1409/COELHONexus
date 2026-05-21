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