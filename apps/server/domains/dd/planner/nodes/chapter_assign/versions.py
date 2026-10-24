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
# v10 (2026-10-14): no reask when a vector misses only ONE chapter and its best score is >= 0.7 (langfuse, 28
# chapters: 47 of 47 last-chapter reasks returned 0.0; the 64 reasks for a cut-off tail 16-27 stay: 76% found >= 0.3).
# v11 (2026-10-14): plans of >= 12 chapters are scored with ONE required key per chapter (c0..cN-1) instead of a score
# list (A/B, 28 chapters: list complete for 30% of pages, fixed keys 100%; 36% faster; agreement with the shipped
# vectors within the task's own run-to-run noise). The list stays as the fallback.
# v12 (2026-10-14): the prompt shows the page's URL path ("PAGE URL PATH: integrations/frameworks/agno-agents") and says to
# weigh it as evidence of the topic (A/B on 166 langfuse pages: page-directory -> top-chapter purity 56% -> 67%; the
# frameworks / model-providers / developer-tools / cookbook sections went to their own chapters 2x more often, not to
# the generic "Observability Features" chapter).
# v13 (2026-10-14): docs that still ended on the lexical fallback get one more pass after a Rotator settle, at 4 slots
# (langfuse run 14: NIM ~6x slower for 40 min, 30 of 310 docs hit the 237 s wall cap twice and were placed by title words).
# v14 (2026-10-14): a vector with every score below 0.3 is asked again, up to 2 times, with a line asking for the closest chapter
# (langfuse run 14: 54 of 310 pages all-zero, every one from nvidia/ising-calibration; a re-ask recovered 33 of 54, the rest
# stay genuine no-fit pages; 22 of 23 control pages kept their best chapter under the new line).
# v15 (2026-10-14): up to 4 zero re-asks (was 2) — fastmcp run 18: 10 of the 17 pages still all-zero flipped within 4 more asks.
PROMPT_VERSION = "v15-zero-reask-4-2026-10-14"
