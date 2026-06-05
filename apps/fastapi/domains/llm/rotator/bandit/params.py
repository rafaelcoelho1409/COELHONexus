from __future__ import annotations


CONTEXT_DIM = 24
CELL_TTL_S = 90 * 24 * 3600

# LinUCB exploration coefficient.
UCB_ALPHA = 0.5

# LinTS posterior-sample scale.
TS_SCALE = 0.5

# Ridge regularization on A_a — also serves as a weak prior θ̂_a → 0.
RIDGE_LAMBDA = 1.0

# Geometric forgetting rate per update (γ). 0.01 → old observations decay to
# e^-1 ≈ 37% after ~100 updates. Drives non-stationarity tracking.
FORGETTING_GAMMA = 0.01


# 2026-05-14 (canary v8): timeout bumped -0.30 → -0.60. Concurrent section-
# cascade queries fire 8-14 picks before any reward lands — a single negative
# observation needs to be strong enough to flip ranking AFTER landing.
ERROR_CLASS_PENALTIES: dict[str, float] = {
    "rate_limit":     -0.10,
    "timeout":        -0.60,
    "server_error":   -0.50,
    "auth_error":     -0.80,
    "schema_invalid": -0.40,
    "content_filter": -0.20,
    "unknown":        -0.40,
}
