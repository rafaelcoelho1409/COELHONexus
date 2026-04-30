"""
Knowledge Distiller — Debug Routes (per-node test harness)

Endpoints:
  GET  /studies/{study_id}/debug/state            checkpoint summary
  POST /studies/{study_id}/debug/run_node         run one node, return patch

Designed for fast iteration on a single node's prompt/logic without
re-running the whole graph (which costs minutes + LLM tokens). State
comes from the LangGraph PostgresSaver checkpoint written by the most
recent full-graph run for this study_id.

NOT for production traffic — these routes execute LLM calls inline
(blocking the request, 5-180s depending on node) and bypass the Celery
queue. Use only during development or post-mortem debugging.
"""
import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

router = APIRouter()


class RunNodeRequest(BaseModel):
    node_name: Literal[
        "planner",
        "canary_synth",
        "synthesize_chapter",
        "curator",
        "critic",
        "assembler",
    ]
    chapter_number: Optional[int] = Field(
        default = None,
        description = (
            "Required when node_name='synthesize_chapter'. Picks which "
            "chapter from the checkpointed plan to re-synthesize."
        ),
    )


@router.get("/studies/{study_id}/debug/state")
async def debug_state(
    study_id: str = Path(..., description = "Study UUID"),
    request: Request = None):
    """
    Return a compact summary of the latest checkpointed state for this
    study. Useful to confirm what nodes have completed and what artifacts
    are available before invoking /debug/run_node.
    """
    from services.knowledge.debug_runner import state_summary
    from services.llm_chain import (
        build_curator_llm,
        build_synth_fallback_chain,
    )

    app = request.app
    checkpointer = getattr(app.state, "checkpointer", None)
    storage = getattr(app.state, "study_storage", None)
    llm = getattr(app.state, "llm", None)
    if checkpointer is None or storage is None or llm is None:
        raise HTTPException(
            status_code = 503,
            detail = "FastAPI dependencies not initialized (checkpointer/storage/llm).",
        )
    synth_llm = build_synth_fallback_chain(groq_timeout_s = 120, nim_timeout_s = 420)
    curator_llm = build_curator_llm(timeout_s = 600)
    try:
        return await state_summary(
            checkpointer = checkpointer,
            storage = storage,
            llm = llm,
            synth_llm = synth_llm,
            curator_llm = curator_llm,
            study_id = study_id,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code = 404, detail = str(e))


@router.post("/studies/{study_id}/debug/run_node")
async def debug_run_node(
    payload: RunNodeRequest,
    study_id: str = Path(..., description = "Study UUID"),
    request: Request = None):
    """
    Execute one KD graph node against the latest checkpointed state.

    Behavior:
      - Reads state from the LangGraph PostgresSaver (`thread_id == study_id`).
      - Calls the node directly with shared FastAPI dependencies (storage,
        main LLM chain). Synth/curator chains are built per-request — they
        share the same builder used by the Celery task.
      - The node's MinIO side-effects (chapter READMEs, validation_report,
        summary.md, etc.) ARE the durable output. We do NOT mutate the
        checkpoint — the next full graph run rebuilds state from scratch.
      - Synthesize_chapter requires `chapter_number` in the body (picks
        which chapter to redo).

    Returns the node's partial state update (same shape it would emit
    inside the full graph). For Pydantic models inside (e.g. ChapterPlan,
    CriticAssessment), FastAPI auto-serializes via .model_dump().

    Wall time per node (rough):
      - planner:      30-90s
      - canary_synth: 30-180s (one chapter)
      - synthesize_chapter: 30-180s per chapter
      - curator:      60-240s (sequential over all chapters)
      - critic:       30-90s
      - assembler:    30-60s
    """
    from services.knowledge.debug_runner import run_single_node
    from services.llm_chain import (
        build_curator_llm,
        build_synth_fallback_chain,
    )

    app = request.app
    checkpointer = getattr(app.state, "checkpointer", None)
    storage = getattr(app.state, "study_storage", None)
    llm = getattr(app.state, "llm", None)
    if checkpointer is None or storage is None or llm is None:
        raise HTTPException(
            status_code = 503,
            detail = "FastAPI dependencies not initialized (checkpointer/storage/llm).",
        )

    # Build synth + curator chains lazily — same params the Celery task uses
    # so debug behavior matches production exactly.
    synth_llm = build_synth_fallback_chain(groq_timeout_s = 120, nim_timeout_s = 420)
    curator_llm = build_curator_llm(timeout_s = 600)

    try:
        patch = await run_single_node(
            checkpointer = checkpointer,
            storage = storage,
            llm = llm,
            synth_llm = synth_llm,
            curator_llm = curator_llm,
            study_id = study_id,
            node_name = payload.node_name,
            chapter_number = payload.chapter_number,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code = 404, detail = str(e))
    except ValueError as e:
        raise HTTPException(status_code = 400, detail = str(e))
    except Exception as e:
        logger.exception(
            f"[debug-run-node] failed: study_id={study_id} "
            f"node={payload.node_name} err={type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code = 500,
            detail = f"{type(e).__name__}: {str(e)[:500]}",
        )

    return {
        "study_id": study_id,
        "node_name": payload.node_name,
        "chapter_number": payload.chapter_number,
        "patch": patch,
    }
