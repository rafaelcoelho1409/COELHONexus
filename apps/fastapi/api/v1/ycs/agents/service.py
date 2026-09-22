"""ycs/agents service — builders for the Ask runtime (graph + LLM clients)."""
from __future__ import annotations
import domains

from fastapi import HTTPException, Request


# In-memory per-pod cancelled-turn registry; upgrade to Redis for >1
# worker. Mark → stream loop observes → clear on finalize.
_CANCELLED_TURN_IDS: set[int] = set()


def mark_turn_cancelled(turn_id: int) -> None:
    _CANCELLED_TURN_IDS.add(turn_id)


def is_turn_cancelled(turn_id: int | None) -> bool:
    return turn_id is not None and turn_id in _CANCELLED_TURN_IDS


def clear_turn_cancelled(turn_id: int | None) -> None:
    if turn_id is not None:
        _CANCELLED_TURN_IDS.discard(turn_id)


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


async def _raise_if_embedding_migration_needed() -> None:
    """2026-09-15: shares the SAME check `api/v1/ycs/content/router.py`
    uses before every Videos-tab dispatch. This endpoint (the Source
    tab's "Continue to Qdrant" follow-up, `static/js/ycs/ingest.js`) and
    `/pipeline` below (when `include_qdrant`) had NO gate at all before
    this — either can write a video's vectors under a DIFFERENT model
    than everything already in the active collection, exactly the
    silent-corpus-fragmentation failure mode the gate exists to
    prevent. See `domains.ycs.embedding_migration.check_migration_needed_now`
    for the actual check."""
    mismatch = await domains.ycs.embedding_migration.service.check_migration_needed_now()
    if mismatch is not None:
        raise HTTPException(
            status_code = 423,
            detail = {
                "error": "embedding_migration_required",
                "message": (
                    f"The configured embedding model changed from "
                    f"{mismatch['from_model']!r} to {mismatch['to_model']!r} "
                    f"since the last ingestion. Start a migration "
                    f"(POST /api/v1/ycs/content/embedding-migration/start) "
                    f"before ingesting more videos."
                ),
                **mismatch,
            },
        )
