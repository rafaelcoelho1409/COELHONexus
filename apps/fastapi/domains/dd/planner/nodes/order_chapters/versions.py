"""order_chapters prompt version — bumped when prompt or scoring policy
changes. Cache invalidates cleanly."""
from __future__ import annotations


# v3 (2026-09-08): timeout_s raised 60s -> 120s, matching Synth's own
# percentile-proven p99/max range on this shared Rotator pool. Confirmed
# live: two consecutive fastapi runs both saw all 3 samples fail with
# RouterRateLimitError "No deployments available" and fall back to
# identity ordering.
# v4 (2026-09-08): timeout_s corrected 120s -> 70s using this node's OWN
# 14-day Langfuse percentiles (p99=50.3s, max=54.9s) instead of Synth's
# borrowed numbers — 120s never fixed this node's 100% failure rate, and
# its own history shows genuine successes never need anywhere near 120s.
# v5 (2026-09-09): SETTLE_DELAY_S corrected 20s -> 130s — same fix as
# doc_distill/chapter_assign; 20s was far shorter than the Rotator's own
# cooldown_time=120 and never reliably outlasted chapter_assign's tail-end
# cooldowns.
PROMPT_VERSION = "v5-settle-130s-2026-09-09"
