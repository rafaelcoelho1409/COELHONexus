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


class GraderCompareRequest(BaseModel):
    """
    Body for POST /debug/grader_compare — side-by-side LLM vs classical
    grader on the same chapter input. Both grades run; both results are
    returned with per-dimension deltas. Phase 1.3 validation harness for
    services/knowledge/grader_classical.py (see
    docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md Phase 1).

    Inputs match the existing `_grade_attempt` signature so any chapter
    that has already been graded (or can be graded) by the LLM path can
    be replayed against the classical path with zero corpus dependency —
    no MinIO read, no Celery enqueue, just two scoring functions on the
    same `synthesis_text`. Pass synthetic fixtures during dev OR paste
    real assembled chapter markdown from a prior committed study.
    """
    synthesis_text: str = Field(
        min_length=10,
        description="Assembled chapter markdown to grade. Same shape "
                    "the LLM grader sees in production.",
    )
    chapter: dict = Field(
        description="ChapterPlan JSON: number, title, goal, assigned_files."
    )
    user_profile: dict = Field(
        description="UserProfile JSON: user_id, level, target_markets, "
                    "portfolio_refs, mastered_technologies, acceptance_threshold.",
    )
    framework: str = Field(
        default="generic",
        description="Framework name (used by LLM grader only).",
    )
    audit_summary: str = Field(
        default="",
        description="Optional deterministic audit signals string. The "
                    "classical scorer parses 'preservation=X.XX' out of this "
                    "for the code_preservation_ratio dim.",
    )


@router.post("/debug/grader_compare")
async def debug_grader_compare(request: GraderCompareRequest):
    """
    Run the LLM grader (existing GRADER_PROMPT path via
    `_invoke_structured_with_fallback`) AND the classical grader
    (services/knowledge/grader_classical.score_chapter_classically) on
    the same chapter input. Return both GraderEvaluation objects + per-
    dimension deltas + wall-clock timings + agreement flags.

    Useful for Phase 1 validation: paste an assembled chapter into the
    body and inspect whether the classical scorer agrees with the LLM
    grader within tolerance. If yes → flip `KD_USE_CLASSICAL_GRADER=1`.
    If no → tune scorers or extend stubs (Phase 1.2 — assumption_match,
    complexity_appropriate, market_analysis).

    Does NOT mutate any pipeline state. Safe to call repeatedly.
    """
    import asyncio as _asyncio
    import time as _time

    from schemas.knowledge.agents import ChapterPlan
    from schemas.knowledge.inputs import UserProfile
    from services.knowledge.grader_classical import score_chapter_classically
    from graphs.knowledge.helpers import _grade_attempt
    from services.llm_chain import build_synth_fallback_chain

    # Parse inputs to Pydantic
    try:
        chapter = ChapterPlan(**request.chapter)
        user_profile = UserProfile(**request.user_profile)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid chapter or user_profile JSON: {type(e).__name__}: {e}",
        )

    # Classical path — 8 deterministic scorers (sub-ms) + 1 small-LLM call
    # for market_analysis (Phase 1.2, ~2-5s via kd-reduce-label rotator).
    t0_classical = _time.monotonic()
    classical_eval = await score_chapter_classically(
        synthesis_text=request.synthesis_text,
        chapter=chapter,
        user_profile=user_profile,
        audit_summary=request.audit_summary,
        framework=request.framework,
    )
    classical_dt = _time.monotonic() - t0_classical

    # LLM path — temporarily DISABLE the KD_USE_CLASSICAL_GRADER env flag
    # so _grade_attempt routes to the LLM grader regardless of current
    # cluster config. This makes the endpoint a true A/B test rather than
    # mirroring whatever the production flag is set to.
    import os as _os
    prior_flag = _os.environ.get("KD_USE_CLASSICAL_GRADER")
    _os.environ["KD_USE_CLASSICAL_GRADER"] = "0"
    t0_llm = _time.monotonic()
    try:
        llm = build_synth_fallback_chain()
        llm_eval = await _grade_attempt(
            synthesis_text=request.synthesis_text,
            chapter=chapter,
            user_profile=user_profile,
            framework=request.framework,
            llm=llm,
            iteration=None,
            study_id=None,
            user_id=None,
            audit_summary=request.audit_summary,
        )
    finally:
        if prior_flag is None:
            _os.environ.pop("KD_USE_CLASSICAL_GRADER", None)
        else:
            _os.environ["KD_USE_CLASSICAL_GRADER"] = prior_flag
    llm_dt = _time.monotonic() - t0_llm

    # Per-dim deltas: classical - llm
    dims = [
        "signal_to_noise", "assumption_match", "job_alignment",
        "citation_integrity", "code_density", "portfolio_synergy",
        "complexity_appropriate", "market_analysis", "code_preservation_ratio",
    ]
    deltas_per_dim = {
        d: round(getattr(classical_eval, d) - getattr(llm_eval, d), 4)
        for d in dims
    }
    weighted_delta = round(classical_eval.weighted_score - llm_eval.weighted_score, 4)
    # Agreement: same action class + scores within 0.10 on every dim
    agreement_action = classical_eval.action == llm_eval.action
    agreement_within_tolerance = all(abs(deltas_per_dim[d]) <= 0.10 for d in dims)

    return {
        "classical": classical_eval.model_dump(),
        "llm": llm_eval.model_dump(),
        "deltas_per_dim": deltas_per_dim,
        "weighted_score_delta": weighted_delta,
        "classical_wall_clock_s": round(classical_dt, 4),
        "llm_wall_clock_s": round(llm_dt, 4),
        "speedup": round(llm_dt / max(classical_dt, 1e-6), 1),
        "agreement_action": agreement_action,
        "agreement_within_0.10_per_dim": agreement_within_tolerance,
    }


class CriticCompareRequest(BaseModel):
    """
    Body for POST /debug/critic_compare — side-by-side LLM vs classical
    critic on one chapter. citation_coverage + code_syntax_valid are
    computed deterministically (both paths share these); only the
    faithfulness dim differs (LLM judgment vs embedding-similarity).
    Phase 2.1 validation harness for services/knowledge/critic_classical.py.

    Single-chapter input keeps the endpoint focused. The production critic
    aggregates across N chapters; this debug version skips aggregation —
    you can replay it per-chapter to see how faithfulness scoring varies.
    """
    chapter_number: int = Field(default=1, ge=1)
    chapter_title: str = Field(default="Chapter")
    chapter_text: str = Field(
        min_length=10,
        description="Assembled chapter markdown.",
    )
    framework: str = Field(default="generic")
    source_contents: dict = Field(
        description="{slug: content} for every cited slug. Loaded by the "
                    "production critic via _read_raw_prefix; pass here for "
                    "test isolation.",
    )
    available_slugs: list[str] = Field(
        default_factory=list,
        description="Slugs available under research/raw/. Used by "
                    "_scan_citations to flag broken/hallucinated cites. "
                    "Defaults to source_contents.keys() if empty.",
    )


@router.post("/debug/critic_compare")
async def debug_critic_compare(request: CriticCompareRequest):
    """
    Run the LLM critic (existing CRITIC_PROMPT path) AND the classical
    critic (services/knowledge/critic_classical.assess_chapter_classically)
    on the same chapter. citation_coverage + code_syntax_valid are shared
    (both paths use the same deterministic computation); only faithfulness
    differs. Return both CriticAssessment objects + per-dim deltas +
    wall-clock timings.

    Useful for Phase 2.1 validation: paste an assembled chapter + its
    source slugs, inspect whether the embedding-similarity faithfulness
    score agrees with the LLM critic within tolerance. If yes → flip
    `KD_USE_CLASSICAL_CRITIC=1`. If no → tune cosine thresholds or
    upgrade to host-side MiniCheck/AlignScore in Phase 2.2.
    """
    import asyncio as _asyncio
    import time as _time
    import os as _os

    from schemas.knowledge.agents import CriticAssessment
    from schemas.knowledge.prompts import CRITIC_PROMPT
    from services.knowledge.critic_classical import (
        assess_chapter_classically,
    )
    from services.llm_chain import build_synth_fallback_chain
    from graphs.knowledge.helpers import (
        _scan_citations,
        _compute_code_syntax_valid_score,
        _invoke_structured_with_fallback,
    )

    # Default available_slugs to source_contents.keys() if not provided.
    available_slugs = (
        set(request.available_slugs)
        if request.available_slugs
        else set(request.source_contents.keys())
    )

    # Build the (chapter_number, title, body) tuple list the deterministic
    # helpers expect.
    chapters_list = [(request.chapter_number, request.chapter_title, request.chapter_text)]

    # Shared deterministic scorers — both paths use these (no point
    # double-computing in side-by-side comparison).
    cited, citation_issues = _scan_citations(chapters_list, available_slugs)
    citation_coverage = (
        sum(1 for s in cited if s in available_slugs) / len(cited)
        if cited else 0.0
    )
    code_syntax_score, ts_stats = _compute_code_syntax_valid_score(chapters_list)

    # Classical path — embedding-similarity faithfulness
    t0_classical = _time.monotonic()
    classical_assessment = await assess_chapter_classically(
        chapter_text=request.chapter_text,
        citation_coverage=citation_coverage,
        code_syntax_valid=code_syntax_score,
        source_contents=request.source_contents,
    )
    classical_dt = _time.monotonic() - t0_classical

    # LLM path — temporarily disable the KD_USE_CLASSICAL_CRITIC env flag
    # so the route is a true A/B test regardless of production config.
    prior_flag = _os.environ.get("KD_USE_CLASSICAL_CRITIC")
    _os.environ["KD_USE_CLASSICAL_CRITIC"] = "0"
    t0_llm = _time.monotonic()
    try:
        llm = build_synth_fallback_chain()
        bundle = (
            f"=== Chapter {request.chapter_number:02d} — "
            f"{request.chapter_title} ===\n{request.chapter_text}\n"
        )
        try:
            llm_assessment_raw = await _invoke_structured_with_fallback(
                prompt=CRITIC_PROMPT,
                llm=llm,
                schema=CriticAssessment,
                invoke_vars={
                    "framework": request.framework,
                    "file_slugs": ", ".join(sorted(available_slugs)),
                    "chapter_bundles": bundle,
                },
                label="debug-critic-compare",
            )
            # Override the LLM's citation_coverage + code_syntax_valid
            # with the deterministic values (same as the production
            # critic does post-OP-59).
            llm_assessment = CriticAssessment(
                citation_coverage=citation_coverage,
                faithfulness=llm_assessment_raw.faithfulness,
                code_syntax_valid=code_syntax_score,
                overall_score=(
                    0.4 * citation_coverage
                    + 0.4 * llm_assessment_raw.faithfulness
                    + 0.2 * code_syntax_score
                ),
                issues=llm_assessment_raw.issues,
            )
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=503,
                detail=f"LLM critic call failed: {type(e).__name__}: {e}",
            )
    finally:
        if prior_flag is None:
            _os.environ.pop("KD_USE_CLASSICAL_CRITIC", None)
        else:
            _os.environ["KD_USE_CLASSICAL_CRITIC"] = prior_flag
    llm_dt = _time.monotonic() - t0_llm

    dims = ["citation_coverage", "faithfulness", "code_syntax_valid", "overall_score"]
    deltas_per_dim = {
        d: round(getattr(classical_assessment, d) - getattr(llm_assessment, d), 4)
        for d in dims
    }
    agreement_within_tolerance = all(abs(deltas_per_dim[d]) <= 0.15 for d in dims)

    return {
        "classical": classical_assessment.model_dump(),
        "llm": llm_assessment.model_dump(),
        "deltas_per_dim": deltas_per_dim,
        "classical_wall_clock_s": round(classical_dt, 4),
        "llm_wall_clock_s": round(llm_dt, 4),
        "speedup": round(llm_dt / max(classical_dt, 1e-6), 1),
        "agreement_within_0.15_per_dim": agreement_within_tolerance,
        "tree_sitter_stats": ts_stats,
    }


class OutlineCompareRequest(BaseModel):
    """
    Body for POST /debug/outline_compare — side-by-side LLM vs classical
    outline generation on the same chapter source. Phase 3.1 validation
    harness for services/knowledge/outline_classical.py.

    Both paths run; both ChapterOutline objects returned with per-field
    deltas + wall-clock timings. Run on synthetic fixtures during dev OR
    paste real chapter assigned-files-content from a prior committed study.
    """
    chapter: dict = Field(
        description="ChapterPlan JSON: number, title, goal, assigned_files."
    )
    files_content: str = Field(
        min_length=10,
        description="Concatenated source files for this chapter (the same "
                    "input `generate_outline` sees in production).",
    )
    framework: str = Field(default="generic")
    tone_block: str = Field(
        default="",
        description="Optional tone-block (UserProfile-derived). Inherits "
                    "neutral tone if empty.",
    )


@router.post("/debug/outline_compare")
async def debug_outline_compare(request: OutlineCompareRequest):
    """
    Run the LLM outline (current OUTLINE_PROMPT path via
    `generate_outline`) AND the classical outline
    (services/knowledge/outline_classical.generate_outline_classically)
    on the same chapter input. Return both ChapterOutline objects +
    per-field diffs + wall-clock timings.

    Useful for Phase 3.1 validation: paste a real chapter's
    `files_content`, inspect whether the classical sections match the
    structural quality of the LLM-emitted ones. Section heading agreement
    matters more than goal text — Phase B routing uses heading + goal
    for vault assignment.
    """
    import time as _time
    import os as _os

    from schemas.knowledge.agents import ChapterPlan
    from services.knowledge.outline_classical import generate_outline_classically
    from graphs.knowledge.hierarchical_synth import generate_outline
    from services.llm_chain import build_synth_fallback_chain

    try:
        chapter = ChapterPlan(**request.chapter)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid chapter JSON: {type(e).__name__}: {e}",
        )

    llm = build_synth_fallback_chain()
    code_vault: dict[str, str] = {}  # outline doesn't need actual code blocks

    # Classical path (deterministic seg + 1 small LLM)
    t0_classical = _time.monotonic()
    classical_outline = await generate_outline_classically(
        chapter=chapter,
        files_content=request.files_content,
        code_vault=code_vault,
        framework=request.framework,
        tone_block=request.tone_block,
        llm=llm,
    )
    classical_dt = _time.monotonic() - t0_classical

    # LLM path — temporarily disable KD_USE_CLASSICAL_OUTLINE so the
    # route is a true A/B test regardless of production config.
    prior_flag = _os.environ.get("KD_USE_CLASSICAL_OUTLINE")
    _os.environ["KD_USE_CLASSICAL_OUTLINE"] = "0"
    t0_llm = _time.monotonic()
    try:
        llm_outline = await generate_outline(
            chapter=chapter,
            files_content=request.files_content,
            code_vault=code_vault,
            framework=request.framework,
            tone_block=request.tone_block,
            llm=llm,
        )
    finally:
        if prior_flag is None:
            _os.environ.pop("KD_USE_CLASSICAL_OUTLINE", None)
        else:
            _os.environ["KD_USE_CLASSICAL_OUTLINE"] = prior_flag
    llm_dt = _time.monotonic() - t0_llm

    return {
        "classical": classical_outline.model_dump(),
        "llm": llm_outline.model_dump(),
        "section_count_delta": (
            len(classical_outline.sections) - len(llm_outline.sections)
        ),
        "flashcard_count_delta": (
            len(classical_outline.flashcards) - len(llm_outline.flashcards)
        ),
        "classical_wall_clock_s": round(classical_dt, 4),
        "llm_wall_clock_s": round(llm_dt, 4),
        "speedup": round(llm_dt / max(classical_dt, 1e-6), 1),
        "classical_section_headings": [s.heading for s in classical_outline.sections],
        "llm_section_headings": [s.heading for s in llm_outline.sections],
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
