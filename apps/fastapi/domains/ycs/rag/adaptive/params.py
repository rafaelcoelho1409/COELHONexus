"""ycs/rag/adaptive — graph-wide tunables.

Mirror of deprecated `graphs/youtube/adaptive.py` inline constants
(`L78, L79, L170, L248, L340`) plus the 2026-06-15 sub-agent
concurrency cap."""
from __future__ import annotations


# Conversation-history truncation in contextualize (deprecated `L78`).
MAX_HISTORY_TURNS = 5

# Answer-truncation in contextualize prompt formatting (deprecated `L79`).
MAX_HISTORY_ANSWER_CHARS = 300

# Recursion limit passed to sub-graph `ainvoke`s (deprecated `L170, L248`).
# 2026-06-15 — lowered from 30 to 12 after a DEEP-mode run where 7/8
# sub-agents looped retrieve → grade → rewrite → retrieve up to 15 times
# each because the grader was rejecting partial JSON outputs. Even with
# the lenient grader rescue in `grader/service.py`, capping the loop is
# the right failsafe.
#
# The 12 is derived from the STANDARD sub-graph's worst-case path with
# `max_retries=1` (which sub-agents pass via `subagent/node.py`):
#   retrieve(1) → grade(2) → generate(3) → hallucination(4) [ungrounded]
#   → rewrite(5) → retrieve(6) → grade(7) → generate(8) → hallucination(9)
#   → format_citations(10) → END
# = 10 nodes worst case, +2 nodes of safety margin = 12. The earlier
# value of 8 was tight enough to fail BEFORE the second hallucination
# check, surfacing as `Subagent error: Recursion limit of 8 reached
# without hitting a stop condition`. 12 lets every grounded-on-retry
# path finish while still bounding stuck sub-agents at ~3 minutes vs
# the original 30-limit's ~10 minutes.
SUBGRAPH_RECURSION_LIMIT = 12

# Critic fallback confidence on structured-output error (deprecated `L340`).
CRITIC_FALLBACK_CONFIDENCE = 0.5

# 2026-06-15 — DEEP-path sub-agent concurrency cap. `plan_research`
# emits 3–5 sub-questions (capped 2026-06-15) which LangGraph `Send`s
# out as fully independent STANDARD sub-pipelines (each runs
# contextualize → retrieve → grade → generate). The original cap of 3
# protected the 8 GiB Helm memory limit from OOM, but it forced the
# rotator to interleave 3 concurrent sub-agents on the same free-tier
# per-minute rate windows (Gemini, NIM kimi, NIM nemotron),
# triggering 429 cascades and the empty-answer placeholders we saw
# across the session.
#
# 2026-06-16 — lowered to 1 (sequential). Trade-off:
#   + Wall-clock: 3 sub-agents now run in 3 waves of 1 instead of 1
#     wave of 3, so a 5-question plan runs in ~5 × T_sub_agent
#     instead of ~⌈5/3⌉ × T_sub_agent = ~2-3× slower.
#   + Quality: each sub-agent gets the full bandit's first-pick
#     model. No contention on rate windows → no 429 cascades →
#     empty-answer placeholders should drop to near-zero.
#   + Memory: peak from ~3 GB to ~1 GB additional → wider OOM margin.
#   + Debuggability: linear log trail; can tell which sub-agent
#     is the culprit when something goes wrong.
# Override via `KD_SUBAGENT_CONCURRENCY` if you want parallel back.
SUBAGENT_CONCURRENCY = 1
