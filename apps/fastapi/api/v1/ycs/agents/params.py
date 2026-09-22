"""ycs/agents params — streaming/timeout/deadline tunables (no behavior)."""
from __future__ import annotations


# Min gap between incremental Postgres writes; smaller = more live, larger = fewer PG round-trips.
STREAM_PERSIST_INTERVAL_S = 2.5

# If no astream() event arrives within this window at bootstrap, fall back to ainvoke()
# (local k3d hangs before the first stream event while ainvoke completes normally).
# 2026-09-16: 15s → 60s. 15s assumed a healthy endpoint (prepare = 2 fast LLM
# calls, first event in seconds). Observed live on a degraded endpoint: prepare
# alone exceeds 15s while the graph is healthy-but-slow, so the fallback fired
# spuriously and switched a good stream to blind ainvoke — plan cards never
# painted, only the spinner, until the whole DEEP run landed at once. 60s keeps
# the live path (plan render + per-card custom flips) through slow patches;
# heartbeats keep TCP alive meanwhile, and the 15-min watchdog still guards a
# genuinely hung producer. Tradeoff accepted: a true k3d-level hang now costs
# 60s of spinner before fallback instead of 15s.
ASTREAM_BOOTSTRAP_FALLBACK_S = 60.0
ASTREAM_BOOTSTRAP_FALLBACK_TICKS = max(
    1, int(ASTREAM_BOOTSTRAP_FALLBACK_S / STREAM_PERSIST_INTERVAL_S),
)

# 15 min ≈ 3× the slowest DEEP sub-agent (recursion_limit=12, cap=3).
LANGGRAPH_WATCHDOG_S = 15 * 60.0

# Global per-request deadlines by requested mode (2026-09-15): bounds
# the total grind during provider outages — DD's backstop principle at
# graph scope. Auto gets the roomy default since it may classify deep;
# forced modes get exact budgets. On expiry the request serves ONE
# bounded `fallback_answer` pass (general knowledge + whatever was
# asked) instead of spinning until the client gives up.
ASK_DEADLINE_S = {
    "fast":     300.0,
    "standard": 600.0,
    "deep":     1500.0,
}
ASK_DEADLINE_DEFAULT_S = 900.0

# Hung PG connection blocks the heartbeat.
PERSIST_TIMEOUT_S = 3.0
