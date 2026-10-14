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
CONCURRENCY = 12
# 2026-10-13: 8 -> 12. The Rotator is far healthier than in September (pool ~70 arms, 1658 OK vs
# 20 wall-cap 504s in a 130-min window) and chapter_assign was 14.5 of fastmcp's 34.5 minutes
# (350 docs / 8 slots); the transient retry below covers the odd benched arm.

# 2026-09-08: 20.0 -> 130.0. Same fix as doc_distill's SETTLE_DELAY_S — the
# original 20s was far shorter than the Rotator Router's own
# cooldown_time=120 (chain/service.py's _get_router()), so it could never
# reliably outlast a deployment benched near the end of doc_distill's run.
# 130s (cooldown_time + 10s buffer) actually guarantees the pool has
# cleared, regardless of when in the prior node's run cooldowns were
# triggered. Skipped entirely on a cache hit.
SETTLE_DELAY_S = 130.0

# A completion cut at MAX_TOKENS is retried once with LENGTH_RETRY_FACTOR x the budget (cap below).
LENGTH_RETRY_FACTOR = 2.0
MAX_TOKENS_RETRY_CAP = 4000
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
# A vector missing at most this many chapters whose best score is >= SKIP_REASK_MIN_BEST is accepted as is.
SKIP_REASK_MAX_MISSING = 1
SKIP_REASK_MIN_BEST = 0.7
# One required key per chapter (schemas.fixed_key_model) instead of a score list, from this many chapters up
# (A/B on 40 langfuse pages, 28 chapters: list complete 11/37, fixed keys 38/38; 16.8 s -> 10.8 s per call;
# best chapter vs the shipped list+reask vectors 66-68% vs 65% for a fresh list answer; run-to-run 72%).
FIXED_KEY_OUTPUT = True
FIXED_KEY_MIN_CHAPTERS = 12
# The page's URL path (without host) goes into the prompt as evidence of its topic.
URL_PATH_IN_PROMPT = True
URL_PATH_MAX_CHARS = 160
# One more try after a transient failure (Rotator 504 / timeout / 5xx / 429) before the
# lexical fallback: 6 of the 8 fallbacks in the 2026-10-13 deepagents + asyncio runs were
# 504s from a hung arm that the Rotator had benched by the time of the retry. A fallback
# misplaces its doc (asyncio `timeout_at` → Event Loop), a retry costs one more call.
TRANSIENT_RETRIES = 1
TRANSIENT_RETRY_PAUSE_S = 4.0
# A page whose whole vector came back below RESCUE_FLOOR is asked again up to this many times (see prompts.CLOSEST_CHAPTER).
ZERO_REASKS = 2
TRANSIENT_KINDS = ("timeout", "server_error", "rate_limit")
# Final sweep: docs that still ended on the lexical fallback get ONE more pass after the main one, at low
# concurrency and after a Rotator settle. langfuse 2026-10-14: NIM answered ~6x slower for 40 minutes, 30 of 310 docs
# (10%) hit the Rotator's 237 s wall cap twice in a row and were placed by title-word overlap (SDK upgrade pages
# into the integration chapters); a sweep runs when the pool is calm, so it recovers them. Skipped when more than
# SWEEP_MAX_SHARE of the docs failed (Rotator down: a sweep would only burn the time) and cut at SWEEP_BUDGET_S.
SWEEP_ENABLED = True
SWEEP_CONCURRENCY = 4
SWEEP_MAX_SHARE = 0.3
SWEEP_BUDGET_S = 600.0

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
