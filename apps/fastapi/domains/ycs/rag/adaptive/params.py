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
