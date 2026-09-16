from __future__ import annotations


MAX_HISTORY_TURNS = 5
MAX_HISTORY_ANSWER_CHARS = 300

# Scoped to sub-agent path (max_retries=1); worst-case is 10 nodes + 2 margin = 12.
SUBAGENT_RECURSION_LIMIT = 12

CRITIC_FALLBACK_CONFIDENCE = 0.5

# Sized to match max sub-question count (5) so a DEEP plan runs all sub-agents in one wave.
SUBAGENT_CONCURRENCY = 5

# 2026-09-16: outer wall-clock deadline on the WHOLE sub-question
# fan-out — see `nodes/subagent/node.py::run_subagents_bounded` for
# why this exists. Without it, LangGraph's `Send()`/superstep barrier
# means synthesize can't start until EVERY sub-question reports back,
# and a single sub-question can legitimately take up to
# ~2 × SUBAGENT_RUNTIME_TIMEOUT_S + REPHRASE_TIMEOUT_S (~20.5 min,
# `run_subagent`'s own documented worst case) — a live 946s (15m46s)
# DEEP run was traced to exactly this: 4 sub-questions finished fast,
# one straggler held the whole response hostage. 240s gives ~2×
# headroom over a healthy multi-hop sub-question (retrieve + grade +
# generate + hallucination, typically well under 2 min) while cutting
# the pathological tail from ~20.5 min down to 4 min. Sub-questions
# still in flight at the deadline are cancelled and placeholdered
# with error_kind="deadline" rather than silently dropped.
DEEP_FANOUT_DEADLINE_S = 240.0
