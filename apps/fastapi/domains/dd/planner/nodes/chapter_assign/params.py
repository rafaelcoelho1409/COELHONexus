"""chapter_assign tunables — concurrency + LLM caps + confidence cutoff."""
from __future__ import annotations


# SOTA Sept 2026: 24×138 burst → 103 lexical fallback (75%) on free-tier 402/timeout;
# 24→12 cuts burst 50% and pooled 200/100 still saturates, 600tok needs 45s not 30s.
# 2026-09-08: cut further 12 -> 8, paired with SETTLE_DELAY_S below. Raising
# the node timeout + Router allowed-fails tolerance did NOT help this node —
# still 126/126 (100%) lexical fallback on a live run, driven by
# RouterRateLimitError "No deployments available" as the FINAL failure
# reason (i.e. genuine upstream provider RPM exhaustion, not litellm's own
# circuit breaker overreacting to transient blips). Smaller bursts reduce
# how hard each per-minute quota window gets hit.
CONCURRENCY = 8

# 2026-09-08: chapter_assign runs immediately after doc_distill, whose own
# burst (even at reduced concurrency) draws down several deployments'
# current-minute RPM quota. A short settle window gives rolling per-minute
# quotas a chance to partially refill before this node's fan-out begins.
# Skipped entirely on a cache hit.
SETTLE_DELAY_S = 20.0

MAX_TOKENS = 1200   # was 600 — scores needs one entry per chapter proposal,
# and PROPOSALS_MAX=30 alone eats ~450-600 tokens of pure JSON in the worst
# case, leaving zero headroom for a reasoning model's <think> preamble (same
# empty-response failure mode confirmed in off_topic/doc_distill).
TEMPERATURE = 0.0
# 2026-09-08: raised 60s -> 120s. Synth's own 14-day Langfuse percentile
# analysis on this exact shared Rotator pool showed genuine successful
# completions running p99=82-119s — 60s was firing false-positive
# APITimeoutErrors on calls that would have succeeded, feeding the Router's
# TimeoutErrorAllowedFails=2 cooldown trigger. Confirmed live: a fastapi run
# hit 132/132 (100%) lexical fallback, driven by cascading
# RouterRateLimitError "No deployments available" once enough deployments
# had been wrongly cooled down by premature timeouts across this and the
# preceding off_topic/doc_distill nodes.
# 2026-09-08: kept at 120s (unlike off_topic/doc_distill/order_chapters,
# which got cut back to 70s). Pulled this node's OWN 14-day Langfuse
# percentiles: genuine successful assignments run p99=54.7s but max=111.4s
# — real calls here (ranking a doc against every chapter proposal, up to
# PROPOSALS_MAX=30 entries) legitimately take much longer at the tail than
# off_topic/doc_distill/order_chapters' smaller calls. 120s is the
# evidence-justified number for this node specifically, not a borrowed
# default.
TIMEOUT_S = 120.0
MAX_REPAIR_ATTEMPTS = 1

BODY_CHARS = 4_000

CONFIDENCE_THRESHOLD = 0.5

# docs with max confidence in [RESCUE_FLOOR, CONFIDENCE_THRESHOLD) get floored up so they're not silently dropped
RESCUE_FLOOR = 0.3

BLOB_PREFIX = "planner"

# Stop-words for the lexical-overlap fallback assignment.
FB_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into",
    "via", "are", "use", "how", "you", "can", "will", "not", "but", "its",
    "has", "langfuse", "data", "api", "sdk", "using", "configure", "setup",
    "guide", "overview", "reference", "documentation", "covering", "usage",
    "concepts",
})
