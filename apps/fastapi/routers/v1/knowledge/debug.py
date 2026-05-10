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


# =============================================================================
# Embeddings smoke test — verify Xinference round-trip + cosine geometry
# =============================================================================
@router.get("/debug/map_compare")
async def debug_map_compare(
    request: Request,
    study_root: str,
    framework: str,
    shard_size: int = 40,
    max_shards: Optional[int] = None,
    skip_off_topic_filter: bool = False,
    classical_only: bool = False,
):
    """
    A/B compare LLM-based MAP vs classical (deterministic) MAP on the same
    corpus, per shard. Reads cached corpus from MinIO, applies the same
    pre-MAP filters the planner uses, builds shards of `shard_size` files,
    then runs both paths over each shard and returns side-by-side output
    for human inspection.

    Query params:
        study_root:  required. e.g. "default/knowledge/terragrunt-0.x.y-..."
        framework:   required. Used by the off-topic filter prototype + LLM prompt.
        shard_size:  optional, defaults to 40 (planner default).
        max_shards:  optional cap. Useful to A/B against just the first
                     N shards (e.g., max_shards=3) on a 400-file corpus.

    Returns JSON with one entry per shard:
      {
        "shard_size": 40,
        "n_files": 440,
        "n_shards": 11,
        "off_topic_dropped": 28,
        "shards": [
          {
            "shard_idx": 1,
            "n_files": 40,
            "llm":       {"clusters": [...], "unused_shard_slugs": [...], "wall_s": 12.4},
            "classical": {"clusters": [...], "unused_shard_slugs": [...], "wall_s":  3.1},
          },
          ...
        ]
      }

    Acceptance gates from KD-PLANNER-MAP-OPTIMIZATION.md §6.2:
      - per-shard cluster count within ±1 of LLM
      - file coverage ≥99% (no dropped slugs)
      - cluster-name semantic overlap ≥80% (manual review)
      - wall time ≤30s per study
      - identical output across reruns (deterministic)
    """
    import time as _t
    from graphs.knowledge.helpers import (
        _dedup_chapter_files,
        _filter_off_topic_files,
        _read_raw_prefix,
    )
    from graphs.knowledge.classical_map import label_shards_classical
    from schemas.knowledge.agents import ShardLabels, ShardCluster
    from schemas.knowledge.prompts import SHARD_LABEL_PROMPT

    app = request.app
    storage = getattr(app.state, "study_storage", None)
    llm = getattr(app.state, "llm", None)
    if storage is None or llm is None:
        raise HTTPException(
            status_code=503,
            detail="FastAPI dependencies not initialized (storage/llm).",
        )

    # --- 1) Load + filter corpus (matches planner pre-MAP path) -----------
    try:
        entries = await _read_raw_prefix(storage, study_root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not entries:
        raise HTTPException(status_code=404, detail=f"empty corpus at {study_root!r}")
    n_initial = len(entries)
    # The semantic off-topic filter embeds the entire corpus (~440 docs on
    # Terragrunt) which can take 1-2 min on Tiger Lake CPU. For A/B debug
    # runs with `max_shards` we usually don't need it — set
    # `skip_off_topic_filter=true` to bypass and go straight to dedup+shard.
    if skip_off_topic_filter:
        n_after_filter = n_initial
    else:
        entries = await _filter_off_topic_files(entries, framework=framework)
        n_after_filter = len(entries)
    entries = _dedup_chapter_files(entries)
    n_after_dedup = len(entries)

    # --- 2) Build shards (matches planner: size 40) ----------------------
    shards = [entries[i:i + shard_size] for i in range(0, len(entries), shard_size)]
    if max_shards is not None:
        shards = shards[:max_shards]

    # --- 3) Per-shard LLM call (minimal — no semaphore/timeout/fallbacks) -
    # Self-contained so we don't have to refactor distiller.py's nested
    # _label_shard. This is for A/B inspection only; production keeps its
    # full retry/timeout/strict-schema pipeline.
    shard_chain = SHARD_LABEL_PROMPT | llm.with_structured_output(
        ShardLabels, method="function_calling",
    )

    async def _llm_one(shard_entries: list[tuple[str, str]], shard_idx: int) -> dict:
        from graphs.knowledge.helpers import _build_corpus_summary
        shard_summary = _build_corpus_summary(shard_entries)
        shard_slugs = [s for s, _ in shard_entries]
        t0 = _t.monotonic()
        try:
            parsed: ShardLabels = await shard_chain.ainvoke({
                "framework": framework,
                "shard_summary": shard_summary,
            })
            wall = _t.monotonic() - t0
            # Drop hallucinated slugs (LLM may invent slugs not in shard)
            for c in parsed.clusters:
                c.file_slugs = [s for s in c.file_slugs if s in shard_slugs]
            return {
                "wall_s": round(wall, 2),
                "clusters": [c.model_dump() for c in parsed.clusters],
                "unused_shard_slugs": list(parsed.unused_shard_slugs or []),
            }
        except Exception as e:
            return {
                "wall_s": round(_t.monotonic() - t0, 2),
                "error": f"{type(e).__name__}: {str(e)[:160]}",
                "clusters": [],
                "unused_shard_slugs": [],
            }

    # Classical path runs as ONE two-phase batch (cluster all → swap once →
    # label all). Single Xinference model transition for the whole batch.
    async def _classical_all(all_shards: list[list[tuple[str, str]]]) -> list[dict]:
        t0 = _t.monotonic()
        try:
            shard_labels_list: list[ShardLabels] = await label_shards_classical(all_shards)
            wall = _t.monotonic() - t0
            per_shard_wall = round(wall / max(len(all_shards), 1), 2)
            return [
                {
                    "wall_s": per_shard_wall,
                    "clusters": [c.model_dump() for c in sl.clusters],
                    "unused_shard_slugs": list(sl.unused_shard_slugs or []),
                }
                for sl in shard_labels_list
            ]
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:160]}"
            return [
                {
                    "wall_s": round(_t.monotonic() - t0, 2),
                    "error": err,
                    "clusters": [],
                    "unused_shard_slugs": [],
                }
                for _ in all_shards
            ]

    # Run both paths in parallel — UNLESS classical_only=true, in which
    # case skip the LLM-rotator path entirely (it can stall for minutes
    # on the kd-all 40-deep cascade through frontier models). The FastHTML
    # /kd/map-compare UI defaults to classical_only=true so the page
    # returns in <30s instead of 5+ minutes.
    import asyncio as _asyncio
    if classical_only:
        classical_results = await _classical_all(shards)
        llm_results = [
            {"wall_s": 0.0, "skipped": True, "clusters": [], "unused_shard_slugs": []}
            for _ in shards
        ]
    else:
        llm_results, classical_results = await _asyncio.gather(
            _asyncio.gather(*(_llm_one(s, i + 1) for i, s in enumerate(shards))),
            _classical_all(shards),
        )

    return {
        "study_root": study_root,
        "framework": framework,
        "n_files_initial": n_initial,
        "n_files_after_off_topic_filter": n_after_filter,
        "n_files_after_dedup": n_after_dedup,
        "off_topic_dropped": n_initial - n_after_filter,
        "dedup_dropped": n_after_filter - n_after_dedup,
        "shard_size": shard_size,
        "n_shards": len(shards),
        "shards": [
            {
                "shard_idx": i + 1,
                "n_files": len(shards[i]),
                "slugs": [s for s, _ in shards[i]],
                "llm": llm_results[i],
                "classical": classical_results[i],
            }
            for i in range(len(shards))
        ],
    }


@router.get("/debug/embeddings_smoke")
async def debug_embeddings_smoke():
    """
    Verify the embeddings stack end-to-end without running the full graph.
    Embeds 3 known phrases (2 similar, 1 different) and asserts the similar
    pair scores higher cosine than the different pair. Returns provider, dim,
    similarity scores. Embeddings now go through the LiteLLM rotator
    (`kd-embed` group → NIM nvidia/llama-nemotron-embed-1b-v2).

    Usage:
      curl http://<fastapi>/api/v1/knowledge/debug/embeddings_smoke
    """
    import asyncio as _asyncio
    from services.knowledge.embeddings import smoke_test
    try:
        # smoke_test is sync; run in worker thread to keep loop responsive.
        result = await _asyncio.to_thread(smoke_test)
        return result
    except Exception as e:
        raise HTTPException(
            status_code = 503,
            detail = f"smoke test failed: {type(e).__name__}: {e}",
        )


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
