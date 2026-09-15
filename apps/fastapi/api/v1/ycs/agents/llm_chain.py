"""ycs/agents — LLM chain factory for YCS Ask (RAG synthesis, grading,
subagents). Talks to the external provider configured on the Settings page
via `build_llm_fallback_chain()` — arm selection/cascade lives server-side
in COELHO LLM Rotator; `rotator_task="ycs-ask"` partitions its per-task
bandit stats away from other workloads. 600s ceiling matches the heaviest
node-level wrapper (subagent 600s); tighter node budgets govern the rest."""
from __future__ import annotations

from domains.llm.rotator.chain import build_llm_fallback_chain


def build_deprecated_llm_chain():
    """Backward-compat name kept for `app.py` lifespan importers."""
    return build_llm_fallback_chain(
        timeout_s    = 600.0,
        rotator_task = "ycs-ask",
    )


def build_fast_llm_chain():
    """Dedicated FAST-mode client (2026-09-15): short outputs only
    (2–5 sentences by prompt contract), so `max_tokens=350` bounds
    cost/latency tails, and `rotator_task="ycs-ask-fast"` lets the
    server-side bandit specialize cheap definition answers apart from
    12k-context synthesis. Timeout comfortably above direct_answer's
    90s node bound (the node governs, this is backstop)."""
    return build_llm_fallback_chain(
        timeout_s    = 120.0,
        max_tokens   = 350,
        rotator_task = "ycs-ask-fast",
    )
