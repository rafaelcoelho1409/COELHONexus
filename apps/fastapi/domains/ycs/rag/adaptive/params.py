from __future__ import annotations


MAX_HISTORY_TURNS = 5
MAX_HISTORY_ANSWER_CHARS = 300

# Scoped to sub-agent path (max_retries=1); worst-case is 10 nodes + 2 margin = 12.
SUBAGENT_RECURSION_LIMIT = 12

CRITIC_FALLBACK_CONFIDENCE = 0.5

# Sized to match max sub-question count (5) so a DEEP plan runs all sub-agents in one wave.
SUBAGENT_CONCURRENCY = 5
