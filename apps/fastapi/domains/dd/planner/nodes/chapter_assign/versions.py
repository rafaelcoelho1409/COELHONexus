"""chapter_assign — bump when prompt or rescue-pass policy changes so re-plans pick up the new logic."""
from __future__ import annotations


# v5 (2026-09-08): timeout_s raised 60s -> 120s, matching Synth's own
# percentile-proven p99/max range on this shared Rotator pool. Confirmed
# live: 132/132 (100%) lexical fallback on a fastapi run, driven by
# cascading RouterRateLimitError "No deployments available" — false-
# positive timeouts here (and in off_topic/doc_distill before it) fed the
# Router's TimeoutErrorAllowedFails=2 cooldown trigger until the whole pool
# was benched simultaneously.
# v6 (2026-09-09): SETTLE_DELAY_S corrected 20s -> 130s — same fix as
# doc_distill; 20s was far shorter than the Rotator's own cooldown_time=120
# and never reliably outlasted doc_distill's tail-end cooldowns.
# v7 (2026-10-13): one transient retry (504/timeout/5xx/429) before the lexical
# fallback; schema-validation failures now reach the reask path (chat_structured_async
# no longer wraps pydantic ValidationError in ChatError).
# v8 (2026-10-13): a score list cut at max_tokens is retried once at 2x the budget instead of
# falling back to the lexical assignment; CONCURRENCY 8 -> 12 (fastmcp assign was 14.5 min).
# v9 (2026-10-13): a partial score vector (~10% of docs on a 24-chapter plan) is completed by a reask
# for the MISSING chapters only, merged into the answer. Measured and rejected: a sparse "list only the
# chapters it supports" output — 4x faster but it forced a home for pages the dense scoring (correctly)
# gave 0.0 everywhere (5 of 7), starving the gap/appendix handling; dense and sparse agreed on the best
# chapter for only ~55% of pages while each agreed with itself ~90%. CONCURRENCY 12 kept: 48-doc
# batches ran 20.8/23.3/29.0/38.3 docs/min at 4/8/6/12 slots on an idle Rotator.
PROMPT_VERSION = "v9-partial-reask-2026-10-13"
