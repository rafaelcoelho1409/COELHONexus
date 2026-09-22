from __future__ import annotations


MAX_HISTORY_TURNS = 5
MAX_HISTORY_ANSWER_CHARS = 300

# Scoped to sub-agent path (max_retries=1); worst-case is 10 nodes + 2 margin = 12.
SUBAGENT_RECURSION_LIMIT = 12

CRITIC_FALLBACK_CONFIDENCE = 0.5

# Sized to match max sub-question count (5) so a DEEP plan runs all sub-agents in one wave.
SUBAGENT_CONCURRENCY = 5

# 2026-09-16: 240s → 600s. 240s assumed a healthy rotator (sub-question
# typically well under 2 min). Observed live the same evening: a degraded
# rotator (TimeoutErrors on nearly every LLM hop, retries recovering) pushed
# EVERY sub-agent past 4 min — 5/5 still running at the deadline, all
# placeholdered, answer synthesized from zero evidence (critic 50%, 0
# sources). 600s keeps the tail cut at less than half the ~20.5-min
# pathological worst case while tolerating ~5× slowdown vs healthy; still
# fits inside the 1500s DEEP stream deadline with room for
# synthesize/critic. Revisit downward if the rotator stabilizes and 600s
# proves to be pure tail-waiting rather than productive grinding.
DEEP_FANOUT_DEADLINE_S = 600.0

# Per-node LLM timeouts (2026-09-15 tiering: 45 → 30s, critic 90 → 60s).
# Each node degrades gracefully (falls back / skips / low-confidence),
# so slow arms fail over fast instead of burning the full budget.
CLASSIFY_TIMEOUT_S = 30.0
CONTEXTUALIZE_TIMEOUT_S = 30.0
CRITIC_TIMEOUT_S = 60.0

# FAST path — tighter ceiling than STANDARD's `generate` because there
# is no retrieval to wait for. If a fast answer doesn't come back inside
# 90 s the model is hung; better to surface an error than spin forever.
DIRECT_ANSWER_TIMEOUT_S = 90.0

# DEEP synthesis takes a long-context input (every sub-question's
# answer concatenated) so it's the slowest single LLM call in the
# graph. 240 s ceiling leaves headroom over a real long-context
# completion while still capping the dead-arm wait.
SYNTHESIZE_TIMEOUT_S = 240.0
