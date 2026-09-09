"""doc_distill tunables — pass-through threshold + LLM caps + cache tag."""
from __future__ import annotations


PASS_THROUGH_THRESHOLD = 80

BODY_CHARS_MAX = 8_000

# SOTA Sept 2026: pooled http2 200/100 16× ~1× latency for 300tok distill; 24
# saturated free-tier general 402/timeout (49+7 in 138). Cutting 20000→8000
# chars cuts TTFT ~40% (tianpan.co) and 24→16 avoids burst 402.
# 2026-09-08: cut further 16 -> 10, paired with SETTLE_DELAY_S below. Raising
# node timeouts + Router allowed-fails tolerance fixed off_topic completely
# (0 errors) but did NOT help doc_distill (75% fallback, still) — most
# doc_distill failures are `rate_limit` as the FINAL reason after all
# retries, i.e. genuine upstream provider RPM quota rejections, not
# litellm's own circuit breaker overreacting. Smaller bursts reduce how
# hard each per-minute quota window gets hit.
CONCURRENCY = 10

# 2026-09-08: doc_distill runs immediately after off_topic, which (post
# timeout fix) now takes ~267s of sustained real LLM traffic to complete
# cleanly — likely still drawing down several deployments' current-minute
# RPM quota when doc_distill's own 16-way (now 10-way) burst starts with
# zero gap. A short settle window gives rolling per-minute quotas a chance
# to partially refill before this node's own fan-out begins. Skipped
# entirely on a cache hit (see doc_distill_run) so a fully-cached re-plan
# pays nothing.
SETTLE_DELAY_S = 20.0

SUMMARY_WORDS_MIN = 8
SUMMARY_WORDS_MAX = 60
KEY_TERMS_MIN = 3
KEY_TERMS_MAX = 8
KEY_TERM_CHARS_MIN = 2
KEY_TERM_CHARS_MAX = 80

MAX_TOKENS = 600     # was 300 — barely covers SUMMARY_WORDS_MAX(60)+KEY_TERMS_MAX(8)
# on its own, leaving ~0 headroom for a reasoning model's <think> preamble
# (same empty-response failure mode diagnosed in off_topic; 97/131 = 74%
# parse_fail on a real run). Doubled for room to actually reason AND answer.
TEMPERATURE = 0.2

MAX_REPAIR_ATTEMPTS = 1

MAX_TRANSIENT_RETRIES = 2
RETRY_BACKOFF_S = (2.0, 5.0)

# 2026-09-08: 60s -> 120s -> 70s. First raised to 120s using Synth's own
# percentile numbers by analogy — that (combined with CONCURRENCY/
# SETTLE_DELAY_S above) fixed doc_distill's fallback rate from 75% to
# 4.7%, but cost real wall time chasing a ceiling this node never actually
# needs. Pulling THIS node's own 14-day Langfuse percentiles showed
# genuine successful distillations top out at p99=47.6s, max=51.7s —
# doc_distill's calls (a 600-token summary+key-terms extraction) are
# nowhere near Synth's larger section-draft calls. 70s gives ~1.35x
# margin over the real max while cutting truly-dead calls in ~40% less
# time than 120s did.
TIMEOUT_S = 70.0

BLOB_PREFIX = "planner"

# Stop-words for the fallback distillate identifier scan.
FB_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into",
    "via", "are", "use", "how", "you", "can", "will", "not", "but", "its",
    "has", "see", "all", "one", "two", "any",
})
