"""doc_distill — bump when prompt or fallback/retry policy changes so re-plans re-distill under the new logic."""
from __future__ import annotations


# v4 (2026-09-08): timeout_s raised 60s -> 120s, matching Synth's own
# percentile-proven p99/max range on this shared Rotator pool. The old 60s
# was firing false-positive APITimeoutErrors that fed the Router's
# TimeoutErrorAllowedFails=2 cooldown trigger, contributing to whole-pool
# RouterRateLimitError cascades observed live on a fastapi corpus run.
# v5 (2026-09-08): timeout_s corrected 120s -> 70s using this node's OWN
# 14-day Langfuse percentiles (p99=47.6s, max=51.7s) instead of Synth's
# borrowed numbers — 120s fixed the fallback rate (75%->4.7%) but overpaid
# on truly-dead calls this node's own history shows it never needed.
# v6 (2026-09-08): SETTLE_DELAY_S corrected 20s -> 130s — 20s was far
# shorter than the Rotator's own cooldown_time=120, so it never reliably
# outlasted off_topic's tail-end cooldowns. Confirmed live: a numpy run's
# very first doc_distill call found the whole pool still exhausted (96.5%
# fallback), which cascaded into chapter_select collapsing the plan to a
# single chapter.
# v7 (2026-09-09): "rate_limit" removed from the retryable-error set — a
# RouterRateLimitError means the whole pool was already checked and found
# empty (120s cooldown), so the old 2-5s retry backoff could never
# succeed. Same fallback outcome either way, ~8s less wasted wait per
# occurrence. timeout/connection remain retryable.
PROMPT_VERSION = "v7-no-retry-rate-limit-2026-09-09"
