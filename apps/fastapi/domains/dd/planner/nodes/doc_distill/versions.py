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
PROMPT_VERSION = "v5-timeout-70s-2026-09-08"
