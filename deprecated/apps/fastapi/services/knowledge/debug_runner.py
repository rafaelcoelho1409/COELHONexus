"""
Knowledge Distiller — Debug Node Runner

Per-node test harness. Runs a single LangGraph node against the latest
checkpointed state for a study_id, without re-running the whole graph or
re-ingesting the corpus.

Use case: iterate on critic.py / synthesizer / curator without paying for
upstream nodes every time. Typical loop:

    1. POST /studies → run full graph once (populates ingest, plan,
       synthesis_results, validation_report, summary, debt as MinIO files
       AND a Postgres checkpoint keyed on study_id).
    2. Edit critic.py → redeploy.
    3. POST /studies/{id}/debug/run_node {"node_name": "critic"} → reads
       checkpoint, calls KnowledgeDistillerGraph.critic(state, llm, storage)
       directly. Critic re-reads chapter READMEs from MinIO, runs LLM,
       writes a fresh validation_report.json.
    4. Inspect MinIO (or call /studies/{id}/debug/state) → see new output.

We do NOT mutate the checkpoint after the node runs. The node's MinIO
side-effects ARE the durable artifacts; orchestration state is rebuilt
from scratch on the next full graph run. Skipping aupdate_state avoids
the operator.add reducer trap on synthesis_results (duplicate-chapter
appends) and keeps the debug path simple.

Synthesize_chapter is the only node that needs a per-chapter payload
(not the full state). Pass `chapter_number` in the request body.

State is loaded via the high-level `graph.aget_state(config)` API so
Pydantic models inside the state (ChapterPlan, UserProfile, etc.) come
back rehydrated — direct `checkpointer.aget_tuple()` returns raw dicts
which would break attribute access (e.g. `chapter.number`).
"""
import logging
from typing import Any, Optional

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from graphs.knowledge.distiller import KnowledgeDistillerGraph
from services.knowledge.cache import StudyCache, canonical_profile_hash
from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


# Nodes the debug runner can invoke. synthesize_chapter requires
# `chapter_number`; the others read full state from the checkpoint.
# Note: ingest is NOT a graph node — corpus is fetched by the dedicated
# /ingestion endpoint, so there is nothing to debug-rerun here.
SUPPORTED_NODES = {
    "planner",
    "canary_synth",
    "synthesize_chapter",
    "curator",
    "critic",
    "assembler",
}


def _build_graph_and_builder(
    *,
    checkpointer: AsyncPostgresSaver,
    storage: MinIOStudyStorage,
    cache: StudyCache,
    llm,
    synth_llm,
    curator_llm,
):
    """
    Build a compiled KD graph wired with the same serializers / checkpointer
    the Celery task uses. The compile is pure-Python (~ms) — no IO. Returns
    `(graph, graph_builder)` so the caller can both read state via
    `graph.aget_state(...)` AND directly invoke node methods on the
    builder instance.
    """
    graph_builder = KnowledgeDistillerGraph()
    graph = graph_builder.build_knowledge_distiller_graph(
        llm = llm,
        storage = storage,
        cache = cache,
        synth_llm = synth_llm,
        curator_llm = curator_llm,
        checkpointer = checkpointer,
    )
    return graph, graph_builder


async def _aget_values(graph, study_id: str) -> dict[str, Any]:
    """
    Read the latest checkpoint values dict for `study_id` via the high-level
    LangGraph API (rehydrates Pydantic models inside state).

    Raises:
        FileNotFoundError: no checkpoint exists for this study_id.
    """
    config = {"configurable": {"thread_id": study_id}}
    snapshot = await graph.aget_state(config)
    if snapshot is None or not snapshot.values:
        raise FileNotFoundError(
            f"No checkpoint for study_id={study_id!r}. Run the full graph "
            f"first via POST /api/v1/knowledge/studies."
        )
    return dict(snapshot.values)


async def run_single_node(
    *,
    checkpointer: AsyncPostgresSaver,
    storage: MinIOStudyStorage,
    llm,
    synth_llm,
    curator_llm,
    study_id: str,
    node_name: str,
    chapter_number: Optional[int] = None) -> dict[str, Any]:
    """
    Execute one KD node against the latest checkpoint state.

    Args:
        checkpointer: AsyncPostgresSaver (FastAPI lifespan instance).
        storage: MinIOStudyStorage (FastAPI lifespan instance).
        llm: main fallback chain.
        synth_llm: synth-only chain (excludes Groq tail).
        curator_llm: pinned curator (GLM-5.1).
        study_id: matches the LangGraph thread_id used by the Celery task.
        node_name: one of SUPPORTED_NODES.
        chapter_number: required ONLY when node_name == "synthesize_chapter".

    Returns the node's partial state update dict (same shape it would
    return inside the full graph) — pass straight back as JSON.

    Raises:
        ValueError: unknown node, missing chapter_number, etc.
        FileNotFoundError: no checkpoint for study_id.
    """
    if node_name not in SUPPORTED_NODES:
        raise ValueError(
            f"Unknown node {node_name!r}. Supported: {sorted(SUPPORTED_NODES)}"
        )

    cache = StudyCache(storage = storage, latest_ttl_days = 14)
    graph, graph_builder = _build_graph_and_builder(
        checkpointer = checkpointer,
        storage = storage,
        cache = cache,
        llm = llm,
        synth_llm = synth_llm,
        curator_llm = curator_llm,
    )
    state = await _aget_values(graph, study_id)

    logger.info(
        f"[debug-run-node] study_id={study_id} node={node_name} "
        f"chapter={chapter_number}"
    )

    if node_name == "planner":
        return await graph_builder.planner(state, llm, storage, cache)

    if node_name == "canary_synth":
        return await graph_builder.canary_synth(state, synth_llm, storage, cache)

    if node_name == "curator":
        return await graph_builder.curator(state, curator_llm, storage)

    if node_name == "critic":
        return await graph_builder.critic(state, llm, storage)

    if node_name == "assembler":
        return await graph_builder.assembler(state, llm, storage)

    # synthesize_chapter — needs a per-chapter payload, not the full state.
    if chapter_number is None:
        raise ValueError(
            "chapter_number is required when node_name='synthesize_chapter'"
        )
    plan = state.get("plan") or []
    chapter = next(
        (c for c in plan if getattr(c, "number", None) == chapter_number),
        None,
    )
    if chapter is None:
        raise ValueError(
            f"chapter_number={chapter_number} not present in checkpointed "
            f"plan (plan length={len(plan)})"
        )
    user_profile = state["user_profile"]
    profile_dict = (
        user_profile.model_dump()
        if hasattr(user_profile, "model_dump")
        else dict(user_profile)
    )
    profile_hash = canonical_profile_hash(profile_dict)
    payload = {
        "chapter": chapter,
        "framework": state["framework"],
        "version": state.get("version") or "latest",
        "profile_hash": profile_hash,
        "user_profile": user_profile,
        "study_root": state["study_root"],
        "study_id": state.get("study_id"),
        "user_id": state.get("user_id"),
        "skip_below_threshold": state.get("skip_below_threshold", False),
    }
    return await graph_builder.synthesize_chapter(
        payload, synth_llm, storage, cache,
    )


async def state_summary(
    *,
    checkpointer: AsyncPostgresSaver,
    storage: MinIOStudyStorage,
    llm,
    synth_llm,
    curator_llm,
    study_id: str) -> dict[str, Any]:
    """
    Lightweight checkpoint inspector for a study. Returns only the
    fields useful for debugging (omits big artifacts like full plan
    objects to keep responses small).
    """
    cache = StudyCache(storage = storage, latest_ttl_days = 14)
    graph, _ = _build_graph_and_builder(
        checkpointer = checkpointer,
        storage = storage,
        cache = cache,
        llm = llm,
        synth_llm = synth_llm,
        curator_llm = curator_llm,
    )
    state = await _aget_values(graph, study_id)
    plan = state.get("plan") or []
    synthesis_results = state.get("synthesis_results") or []
    raw_files = state.get("raw_files") or []
    return {
        "study_id": state.get("study_id"),
        "framework": state.get("framework"),
        "version": state.get("version"),
        "study_root": state.get("study_root"),
        "current_phase": state.get("current_phase"),
        "ingest_tier_used": state.get("ingest_tier_used"),
        "raw_files_count": len(raw_files),
        "plan_chapters": [
            {
                "number": getattr(c, "number", None),
                "title": getattr(c, "title", None),
                "assigned_files_count": len(getattr(c, "assigned_files", []) or []),
            }
            for c in plan
        ],
        "synthesis_results_count": len(synthesis_results),
        "synthesis_chapter_numbers": [
            r.get("number") for r in synthesis_results if isinstance(r, dict)
        ],
        "canary_chapter_number": state.get("canary_chapter_number"),
        "validation_report_present": state.get("validation_report") is not None,
        "summary_path": state.get("summary_path"),
        "debt_path": state.get("debt_path"),
    }
