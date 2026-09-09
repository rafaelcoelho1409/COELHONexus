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
PROMPT_VERSION = "v6-settle-130s-2026-09-09"
