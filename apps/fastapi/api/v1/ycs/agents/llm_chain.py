"""ycs/agents — LLM chain factory for YCS Ask (RAG synthesis, grading,
subagents). Talks to the external endpoint configured on the Settings page
via `build_chat_model()`. 600s ceiling matches the heaviest node-level
wrapper (subagent 600s); tighter node budgets govern the rest."""
from __future__ import annotations

import domains


def build_deprecated_llm_chain():
    """Backward-compat name kept for `app.py` lifespan importers."""
    return domains.settings.chat.service.build_chat_model(
        timeout_s    = 600.0,
    )


def build_fast_llm_chain():
    """Dedicated FAST-mode client (2026-09-15): short outputs only
    (2–5 sentences by prompt contract), so `max_tokens=350` bounds
    cost/latency tails. Timeout comfortably above direct_answer's
    90s node bound (the node governs, this is backstop)."""
    return domains.settings.chat.service.build_chat_model(
        timeout_s    = 120.0,
        max_tokens   = 350,
    )
