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
