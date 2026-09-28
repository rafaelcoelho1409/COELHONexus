"""doc_distill tunables — LLM caps + cache tag."""
from __future__ import annotations


BODY_CHARS_MAX = 100_000   # 2026-09-27: 8000 -> 100000, matching
# `synth/nodes/digest/service.py::_MAX_SOURCE_CHARS` — that node reads whole
# source pages at this same cap ("generous... most pages are <30K chars; cap
# at 100K to be safe") and is the right reference here: unlike off_topic's
# head+tail truncation (a cited ACCURACY optimization for binary
# classification — full body would make it WORSE), doc_distill's old 8000
# cap had no such backing, just latency tuning ("cuts TTFT ~40%"). This is a
# SUMMARIZATION task — content past the old cutoff (a later API section, a
# code example) was silently invisible to the summary + key_terms that
# chapter_propose builds seeds from. Retried at half this cap on a transient
# failure (see BODY_CHARS_RETRY_DIVISOR) — same shrink-on-retry shape digest
# already uses.
BODY_CHARS_RETRY_DIVISOR = 2

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

# 2026-09-08: 20.0 -> 130.0. The original 20s was arithmetically
# guaranteed to be unreliable: the Rotator's own Router sets
# cooldown_time=120 (chain/service.py's _get_router(), this session's
# external rotator repo) — any deployment benched in roughly the last 100s
# of off_topic's run is still cooling 20s later, so whether this "worked"
# was a coin-flip on exactly when off_topic's last cooldown got triggered,
# not a real guarantee. Confirmed live: a fastapi run happened to land
# lucky (doc_distill succeeded 95%); a numpy run landed unlucky (doc_distill's
# very first call found the WHOLE pool still in cooldown, 96.5% fallback,
# which cascaded into chapter_select collapsing the entire plan to 1
# chapter). 130s (cooldown_time + 10s buffer) actually outlasts the
# cooldown window regardless of when in the prior node's run it was
# triggered — a real guarantee, not a hope. Skipped entirely on a cache
# hit (see doc_distill_run) so a fully-cached re-plan pays nothing.
SETTLE_DELAY_S = 130.0

SUMMARY_WORDS_MIN = 8
SUMMARY_WORDS_MAX = 60
KEY_TERMS_MIN = 3
KEY_TERMS_MAX = 8
KEY_TERM_CHARS_MIN = 2
KEY_TERM_CHARS_MAX = 80

MAX_TOKENS = 1_200   # was 300 -> 600 -> 1200. 600 barely covered
# SUMMARY_WORDS_MAX(60)+KEY_TERMS_MAX(8) on its own, leaving ~0 headroom for
# a reasoning model's <think> preamble (same empty-response failure mode
# diagnosed in off_topic; 97/131 = 74% parse_fail on a real run). Doubled
# again 2026-09-27 alongside the BODY_CHARS_MAX raise (8000->100000) — a
# richer/longer source page plausibly means a longer reasoning preamble
# before the model reaches its (still tiny) JSON answer; the answer itself
# doesn't grow, so this stays far below digest's 6000 (sized for its much
# larger structured output, not input size).
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
# 2026-09-27: 70s -> 120s. Those percentiles were measured at the old
# 8000-char body cap; raising BODY_CHARS_MAX to 100000 invalidates that
# baseline — prefill time scales with input tokens regardless of Rotator
# throughput. 120s matches `synth/nodes/digest`'s own timeout for
# processing input at this same size (its `_MAX_SOURCE_CHARS` is the same
# 100000), which is real measured data for this input-size regime rather
# than a guess.
TIMEOUT_S = 120.0

BLOB_PREFIX = "planner"

# Stop-words for the fallback distillate identifier scan.
FB_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into",
    "via", "are", "use", "how", "you", "can", "will", "not", "but", "its",
    "has", "see", "all", "one", "two", "any",
})

# Failure reasons worth retrying (vs failing straight to the fallback
# distillate). timeout/connection genuinely clear within seconds; rate
# limits from a shared pool do not — retrying those just burns ~8s per
# occurrence for an identical outcome (confirmed live: 133/166 docs).
TRANSIENT_REASONS = frozenset({"timeout", "connection"})
