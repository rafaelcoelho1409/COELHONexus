"""ycs/agents service — builders for the Ask runtime (graph + LLM clients)."""
from __future__ import annotations

from fastapi import Request

import domains


async def build_graph_from_request(request: Request):
    """Build the adaptive RAG graph wired to `app.state.llm` (chat model, not BYOK override).
    BYOK exclusive-override was dropped — routing one user key through a shared
    fallback defeats explicit provider choice; same workload as Planner/Synth so same endpoint path."""
    app = request.app
    return domains.ycs.rag.adaptive.graph.build_adaptive_rag_graph(
        retriever    = app.state.smart_retriever,
        grader       = app.state.grader,
        llm          = app.state.llm,
        checkpointer = None,
        neo4j_graph  = app.state.neo4j_graph,
        llm_fast     = getattr(app.state, "llm_fast", None),
    )


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
