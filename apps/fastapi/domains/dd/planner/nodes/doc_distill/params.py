"""doc_distill tunables — pass-through threshold + LLM caps + cache tag."""
from __future__ import annotations


# Corpora at-or-below this threshold skip the distillation step (the
# proposer can ingest raw bodies directly). Above this, each doc gets a
# 1-sentence summary + key terms.
PASS_THROUGH_THRESHOLD = 80

# Per-doc body cap when feeding the summarizer (chars). Generous since
# the summarizer call is cheap; truncating only matters for very long
# README-style docs.
BODY_CHARS_MAX = 20_000

# Concurrency for parallel doc summarization.
# 2026-05-27 P1 — lowered 16 → 8 after Claude Code Run produced 36%
# rate-limit failures on NIM+Mistral with 16 simultaneous workers. The
# bandit's top-5 cascade kept hitting saturated arms across all 5
# deployments. 8 keeps the burst inside both provider buckets' sustained
# capacity. Wall-time penalty is small (~+30 sec on N=132) vs the quality
# penalty of 48 dropped docs at 16.
CONCURRENCY = 8

# Summary length bounds (Pydantic-enforced).
SUMMARY_WORDS_MIN = 8
SUMMARY_WORDS_MAX = 60
KEY_TERMS_MIN = 3
KEY_TERMS_MAX = 8
KEY_TERM_CHARS_MIN = 2
KEY_TERM_CHARS_MAX = 80

# LLM call settings.
MAX_TOKENS = 300
TEMPERATURE = 0.2

# Repair retries per doc on Pydantic-fail. Higher than usual because
# summaries are short and any miss is cheap to retry.
MAX_REPAIR_ATTEMPTS = 1

# 2026-06-05 — Transient-error retry budget per doc.
#
# Memory `project_planner_cc_coverage_2026_05_29` flagged 17 silent
# distill failures on the Claude Code corpus with NO retry path before
# the deterministic fallback. The fallback is a safety net, but a real
# distillate (when retry succeeds) gives chapter_propose + chapter_assign
# a far better routing signal than the title-derived stub.
#
# We retry only on TRANSIENT errors (rate-limit, timeout) — the bandit
# rotates to a different deployment on the retry, so a saturated NIM /
# Groq arm typically clears within one attempt. Non-transient errors
# (validation, auth, context-length) fall through immediately.
MAX_TRANSIENT_RETRIES = 2
# Exponential-ish backoff between transient retries (seconds).
RETRY_BACKOFF_S = (2.0, 5.0)

BLOB_PREFIX = "planner"

# Stop-words for the fallback distillate identifier scan.
FB_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into",
    "via", "are", "use", "how", "you", "can", "will", "not", "but", "its",
    "has", "see", "all", "one", "two", "any",
})
