"""ycs/rag/adaptive — graph-wide tunables.

Mirror of deprecated `graphs/youtube/adaptive.py` inline constants
(`L78, L79, L170, L248, L340`) plus the 2026-06-15 sub-agent
concurrency cap."""
from __future__ import annotations


# Conversation-history truncation in contextualize (deprecated `L78`).
MAX_HISTORY_TURNS = 5

# Answer-truncation in contextualize prompt formatting (deprecated `L79`).
MAX_HISTORY_ANSWER_CHARS = 300

# Recursion limit passed when SUB-AGENTS invoke the STANDARD sub-graph
# (DEEP-mode fan-out). Sized for `max_retries=1` (the value sub-agents
# pin in `subagent/node.py::_STANDARD_GRAPH_CONFIG`), NOT for the
# STANDARD-mode top-level path which keeps `max_retries=3` and uses
# `standard/params.py::DEFAULT_RECURSION_LIMIT=30` instead.
#
# 2026-06-16 — renamed from `SUBGRAPH_RECURSION_LIMIT` after a STANDARD
# run blew the 12-limit. The previous name implied "use for every sub-
# graph invocation" but the value was sized for the sub-agent's
# `max_retries=1` budget. STANDARD with `max_retries=3` needs ~20
# nodes worst-case — silent reuse of the 12 cap looked like a stuck
# pipeline. `run_standard/node.py` now imports the STANDARD pipeline's
# own `DEFAULT_RECURSION_LIMIT` directly; this constant stays scoped
# to the sub-agent path.
#
# Sub-agent worst-case path derivation (max_retries=1):
#   retrieve(1) → grade(2) → generate(3) → hallucination(4) [ungrounded]
#   → rewrite(5) → retrieve(6) → grade(7) → generate(8) → hallucination(9)
#   → format_citations(10) → END
# = 10 nodes worst case, +2 nodes of safety margin = 12.
SUBAGENT_RECURSION_LIMIT = 12

# Critic fallback confidence on structured-output error (deprecated `L340`).
CRITIC_FALLBACK_CONFIDENCE = 0.5

# 2026-06-16 (revised) — DEEP-path sub-agent concurrency cap.
# `plan_research` emits 3–5 sub-questions (cap 5, see
# `nodes/classify/schemas.py`) which LangGraph `Send`s out as fully
# independent STANDARD sub-pipelines (each runs retrieve → grade →
# generate → hallucination → cite). Only the DEEP path uses
# `Send(run_subagent)`; FAST and STANDARD never fan out, so this knob
# only affects DEEP runs by construction.
#
# Sized to match the max sub-question count so a typical DEEP plan
# runs ALL sub-agents in ONE wave instead of N sequential waves.
# Expected wall-time: ~T_sub_agent instead of N × T_sub_agent —
# 3-5× speed-up over the previous sequential default (cap=1).
#
# Why this is safe under the free-tier rotator (the cap=1 docstring
# called this out as risky; the analysis there was incomplete):
#
#   • Pool depth — `dd-all` ships ~21 arms across 7 providers (NIM /
#     Gemini / Mistral / Groq / Cerebras / DeepSeek / SambaNova). The
#     Router's `simple-shuffle` picks a random arm per call, so 5
#     concurrent sub-agents land on 5 distinct arms with ~95% prob
#     (1 - (1-1/21)^5 collision-free) and across at LEAST 3 distinct
#     providers in the common case.
#
#   • Grader gate inside each sub-agent — `ycs/grader/params.py`
#     caps grading-LLM calls at GRADER_CONCURRENCY=2 PER sub-agent.
#     Peak DEEP-mode in-flight = 5 sub-agents × 2 grader slots = 10
#     calls, sub-linear in the 21-arm pool depth.
#
#   • 429 self-healing — `chain/service.py::_arm_cooldown` puts a
#     429-hitting arm in a 60 s in-process cool-down. The bandit's
#     `predict_top_k` skips cooling arms; the LiteLLM Router's
#     `allowed_fails=3` per-deployment cooldown takes the bad arm
#     fully offline. A 429 storm is bounded to one burst-window per
#     arm, not a cascade.
#
#   • Auto-retry — `_RotatorAutoRetryRouter` catches NotFoundError /
#     EOL / empty-generations and forces a Router reshuffle. A
#     dead-arm pick on one sub-agent doesn't poison the others.
#
# Quality posture (the user's explicit constraint — "speed without
# sacrificing quality"): the bandit ranking, the per-arm 60s cool-
# down, and the cross-provider distribution mean each sub-agent
# still gets a HIGH-RANKED arm on its first pick. We are NOT
# downgrading to a faster-but-weaker tier; we are PARALLELIZING the
# existing pool. Per-sub-agent quality is unchanged.
#
# Failure-mode hedge — if a single user has only enabled 1-2
# providers via BYOK (so the pool collapses to ~3-6 arms), 5
# concurrent sub-agents will saturate the per-minute window of
# those few arms. In that case set `KD_SUBAGENT_CONCURRENCY=2` (or
# =1 to fully serialize). The env override wins over this default —
# see `graph.py::_resolve_subagent_concurrency`.
SUBAGENT_CONCURRENCY = 5
