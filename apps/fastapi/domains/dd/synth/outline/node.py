"""outline_sdp — SurveyGen-I PlanEvo Structure-Driven Planner.

Step 3 of the synth pipeline (per
`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` + the outline_sdp deep
research report). The first LLM-driven synth graph node.

WHAT IT DOES (per chapter):

  1. Loads the planner's `plan-latest.json` for the framework.
  2. Picks the chapter by `chapter_id` (set in state at kick-off).
  3. Reads each source page (already corpus_normalized + vault_sentinelized
     at ingestion-time, per the 2026-05-19 architecture cleanup).
  4. Concatenates source bodies + estimates vault size.
  5. Generates N=_N_SAMPLES candidate outlines via the dd-grader bandit
     rotator (3 distinct rotator picks for MAMM diversity).
  6. Validates each candidate's Pydantic shape + structural rules
     (banned headings, DAG validity, max depth).
  7. USC vote — one extra LLM call picks the SINGLE BEST candidate by
     a structural rubric (no violations > section count > DAG shape
     > heading specificity > description quality).
  8. Up to `_MAX_REPAIR_RETRIES` repair attempts on the winner if it
     has structural issues.
  9. Persists the validated ChapterOutline + derived OutlineDAG as
     a MinIO blob (versioned + latest pointer), keyed by a manifest
     hash over (sources, planner version, prompt version).
 10. Returns state patch with `outline_path` + `outline_stats`.

CACHING — content-addressed:

  versioned: synth/{slug}/{chapter_id}/outline/{manifest_hash}.json
  latest:    synth/{slug}/{chapter_id}/outline-latest.json

  Manifest hash = sha256[:16] of:
      sources_sha (sorted source MinIO keys)
    | n_sources
    | sources_bytes (total chars after concat)
    | chapter_title
    | chapter_desc
    | prompt_version
    | schema_version

  Cache hit returns immediately + emits `done` SSE with cache_hit=true.

FAIL-SOFT BEHAVIOR (matches reduce.py's pattern):

  - all N samples fail to parse: emit a minimal fallback outline with
    sections derived heuristically from H1/H2 in the source, log
    error, write artifact with error flag, return non-error state.
    Downstream nodes can still run on the fallback; mgsr_replan will
    likely flag it heavily but won't crash.
  - repair retries exhausted: persist the best-seen invalid candidate
    with violations flagged; mgsr_replan will retry the structural
    fixes in the next iteration.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import chat_judge_bandit_async

from ..observability.spans import traced
from .constants import (
    OUTLINE_PROMPT_VERSION,
    OUTLINE_SCHEMA_VERSION,
)
from .types import (
    ChapterOutline,
    OutlineDAG,
    OutlineSection,
)
from .service import (
    build_outline_prompt,
    build_repair_prompt,
    build_usc_vote_prompt,
    count_vault_sentinels,
    derive_dag,
    summarize_candidate,
    validate_outline_structure,
)
from ..progress import emit_progress
from ..state import SynthState


logger = logging.getLogger(__name__)


# =============================================================================
# Tunables — quality > speed (per project memory feedback_kd_quality_over_speed)
# =============================================================================
_N_SAMPLES               = 3
_TEMPERATURE_DRAFT       = 0.4   # diversity for USC candidates
_TEMPERATURE_VOTE        = 0.0
_TEMPERATURE_REPAIR      = 0.2
_MAX_REPAIR_RETRIES      = 3
_MAX_TOKENS_DRAFT        = 8000
_MAX_TOKENS_VOTE         = 200
_MAX_TOKENS_REPAIR       = 8000
# Source budget for the prompt (chars). Free-tier rotator pool includes
# 200K-1M context models, so 180K chars (≈45K tokens) is a safe ceiling
# that fits even the smallest 64K-context arm with comfortable margin
# for the schema + rules block.
_MAX_SOURCE_CHARS        = 180_000
_SOURCE_CONCAT_SEPARATOR = "\n\n---\n\n"
_TARGET_SECTIONS_HINT    = 8

# DD-SYNTH-SPEED-SOTA #A2 (2026-05-26) — structured-output schema for the
# outline candidate generator + repair calls. USC vote uses a smaller
# {"chosen_index": int} payload — kept as json_object (no Pydantic schema)
# so the picker stays prompt-driven.
_OUTLINE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "chapter_outline",
        "schema": ChapterOutline.model_json_schema(),
        "strict": False,
    },
}
_USC_VOTE_RESPONSE_FORMAT = {"type": "json_object"}

# DD-SYNTH-SPEED-SOTA #B2 (2026-05-26) — Optimal-Stopping on outline
# candidate generation. Fire candidate 1; if it passes the deterministic
# gate (parsed cleanly + zero validation issues + reasonable section
# count), ship it directly. Else fan out remaining N-1 candidates and
# run the USC vote picker. Flag-gated via KD_OUTLINE_OPTIMAL_STOPPING
# (default true). Same pattern as sawc/node.py Optimal-Stopping.
_OUTLINE_OPTIMAL_STOPPING_MIN_SECTIONS = 5
_OUTLINE_OPTIMAL_STOPPING_ENABLED = os.environ.get(
    "KD_OUTLINE_OPTIMAL_STOPPING", "true",
).lower() in ("true", "1", "yes", "on")

_BLOB_PREFIX = "synth"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# =============================================================================
# Blob keys
# =============================================================================
def _versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return (
        f"{_BLOB_PREFIX}/{slug}/{chapter_id}/outline/{manifest_hash}.json"
    )


def _latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/outline-latest.json"


def _planner_latest_key(slug: str) -> str:
    return f"planner/{slug}/plan-latest.json"


# =============================================================================
# Helpers
# =============================================================================
def _parse_json_response(text: str) -> Optional[dict]:
    """Best-effort JSON extraction. Tolerates ```json fences + leading
    prose. Same approach as planner/reduce/service.py."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _try_parse_outline(
    raw: dict,
) -> tuple[Optional[ChapterOutline], Optional[str]]:
    """Pydantic-validate raw dict → ChapterOutline. Returns (outline, error)."""
    try:
        outline = ChapterOutline.model_validate(raw)
        return outline, None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def _shorten_pydantic_error(e: ValidationError) -> str:
    """Compact a Pydantic ValidationError into a 200-char summary that's
    still actionable in repair-prompt feedback."""
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:4]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 4} more)" if len(errs) > 4 else ""
    return "; ".join(lines) + suffix


def _concat_sources(bodies: list[str]) -> tuple[str, bool]:
    """Concatenate source markdown bodies with separators, capped at
    `_MAX_SOURCE_CHARS`. Returns (concat_text, truncated_flag)."""
    parts: list[str] = []
    total = 0
    truncated = False
    for body in bodies:
        if not body:
            continue
        if total + len(body) > _MAX_SOURCE_CHARS:
            remaining = _MAX_SOURCE_CHARS - total
            if remaining > 200:
                parts.append(body[:remaining])
                total = _MAX_SOURCE_CHARS
            truncated = True
            break
        parts.append(body)
        total += len(body) + len(_SOURCE_CONCAT_SEPARATOR)
    return _SOURCE_CONCAT_SEPARATOR.join(parts), truncated


def _heuristic_fallback_outline(md_text: str) -> ChapterOutline:
    """Last-resort: derive sections from H1/H2 in the source. Emitted
    when all N samples fail to parse. Downstream mgsr_replan will
    inevitably rewrite this, but having SOMETHING valid keeps the
    chapter graph runnable instead of poisoning the whole pipeline."""
    headings = re.findall(r"(?m)^#{1,3}\s+(.+)$", md_text or "")
    cleaned: list[str] = []
    seen: set[str] = set()
    for h in headings:
        h = h.strip().rstrip("#").strip()
        if not h:
            continue
        key = h.casefold()
        if key in seen:
            continue
        seen.add(key)
        # Trim to 8 words
        words = h.split()
        if len(words) > 8:
            h = " ".join(words[:8])
        # Skip banned content-types
        if key in {"introduction", "overview", "summary", "conclusion"}:
            continue
        cleaned.append(h)
        if len(cleaned) >= 8:
            break

    while len(cleaned) < 4:
        cleaned.append(f"Topic {len(cleaned) + 1}")

    sections = [
        OutlineSection(
            section_id=f"s{i + 1}",
            heading=h if len(h.split()) >= 2 else f"{h} Concepts",
            description=(
                f"Auto-derived section from source heading {h!r}; "
                "synthesized as fallback after LLM outline generation "
                "failed. Refine in MGSR."
            ),
            prerequisites=[f"s{i}"] if i > 0 else [],
            needs_code=True,
        )
        for i, h in enumerate(cleaned)
    ]
    return ChapterOutline(
        sections=sections,
        challenges=[
            "What is the primary concept introduced in this chapter?",
            "Explain how the framework handles its main task.",
            "Walk through one example end-to-end.",
            "What is the most common error mode and how do you debug it?",
            "How would you extend this for a new use case?",
        ],
        flashcards=[
            {"q": "What is the chapter's core abstraction?",
             "a": "See source documentation — outline generation fell back."},
            {"q": "Where do you start when using this framework?",
             "a": "See source documentation — outline generation fell back."},
            {"q": "What is the most-used API surface?",
             "a": "See source documentation — outline generation fell back."},
            {"q": "What configuration options matter most in production?",
             "a": "See source documentation — outline generation fell back."},
        ],
    )


def _serialize_outline_with_dag(
    outline: ChapterOutline, dag: OutlineDAG,
) -> dict:
    """Combine outline + dag for MinIO persistence. Schema:
        {schema_version, prompt_version, outline: <ChapterOutline>,
         dag: <OutlineDAG>}
    Edges and stages are JSON-friendly already (tuples → lists, dict
    keys → str)."""
    return {
        "schema_version": OUTLINE_SCHEMA_VERSION,
        "prompt_version": OUTLINE_PROMPT_VERSION,
        "outline":        outline.model_dump(),
        "dag": {
            "edges":         [list(e) for e in dag.edges],
            "stage_index":   dag.stage_index,
            "stages":        {str(k): v for k, v in dag.stages.items()},
            "max_stage":     dag.max_stage,
            "removed_edges": [list(e) for e in dag.removed_edges],
        },
    }


# =============================================================================
# Sample generation pipeline
# =============================================================================
async def _draft_one_outline(
    prompt: str,
    *,
    sample_idx: int,
    n_total: int,
    thread_id: str,
) -> tuple[Optional[dict], dict]:
    """One LLM call via the dd-grader bandit rotator. Returns (parsed_dict
    or None, meta dict with deployment/latency/attempts/reward/error).

    Emits a `sample_done` SSE event when this individual sample completes
    so the UI shows per-sample progress instead of going silent for 30s
    while asyncio.gather awaits all 3 samples in parallel."""
    t0 = time.monotonic()
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_DRAFT,
            temperature=_TEMPERATURE_DRAFT,
            response_format=_OUTLINE_RESPONSE_FORMAT,
        )
    except Exception as e:
        await emit_progress(
            thread_id, "outline_sdp", "sample_done",
            sample_idx=sample_idx, n_total=n_total,
            ok=False, error=f"{type(e).__name__}: {str(e)[:120]}",
            wall_ms=int((time.monotonic() - t0) * 1000),
        )
        return None, {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    parsed = _parse_json_response(response)
    if not parsed:
        await emit_progress(
            thread_id, "outline_sdp", "sample_done",
            sample_idx=sample_idx, n_total=n_total,
            ok=False, error="parse_failed",
            wall_ms=int((time.monotonic() - t0) * 1000),
            deployment=meta.get("deployment"),
        )
        return None, {
            **meta,
            "error": "parse_failed",
            "raw":   (response or "")[:200],
        }
    await emit_progress(
        thread_id, "outline_sdp", "sample_done",
        sample_idx=sample_idx, n_total=n_total,
        ok=True,
        wall_ms=int((time.monotonic() - t0) * 1000),
        deployment=meta.get("deployment"),
        n_sections=len(parsed.get("sections") or []),
    )
    return parsed, meta


async def _generate_samples(
    prompt: str, n: int, thread_id: str,
    *,
    n_sources: int | None = None,
) -> list[tuple[dict, dict]]:
    """Fire N drafts (sequential w/ early-exit OR concurrent fan-out).

    DD-SYNTH-SPEED-SOTA #B2 (2026-05-26) — Optimal-Stopping: fire sample 1
    first; if it parses cleanly, passes structure validation with zero
    issues, AND has >= _OUTLINE_OPTIMAL_STOPPING_MIN_SECTIONS sections, ship
    it alone and skip the remaining N-1 samples. Else fan out remaining
    concurrently and let USC vote decide. arXiv 2510.01394 (Oct 2025):
    15-35% sample reduction at equal Best-of-N quality. Disabled via
    `KD_OUTLINE_OPTIMAL_STOPPING=false`.

    Failures (parse fail, None payload) are logged but don't block the rest.
    Each sample emits a `sample_done` SSE event on completion so the UI
    sees steady progress through the long-running LLM phase.
    """
    if _OUTLINE_OPTIMAL_STOPPING_ENABLED and n >= 2:
        r0 = await _draft_one_outline(
            prompt, sample_idx=0, n_total=n, thread_id=thread_id,
        )
        results: list = [r0]
        parsed0, _meta0 = r0
        if parsed0 is not None:
            outline0, _err = _try_parse_outline(parsed0)
            if outline0 is not None:
                dag0 = derive_dag(outline0.sections)
                _, issues0 = validate_outline_structure(
                    outline0, dag0, n_sources=n_sources,
                )
                if (
                    not issues0
                    and len(outline0.sections)
                        >= _OUTLINE_OPTIMAL_STOPPING_MIN_SECTIONS
                ):
                    logger.info(
                        f"[outline_sdp] Optimal-Stopping fired — sample 0 "
                        f"clean ({len(outline0.sections)} sections, 0 issues); "
                        f"skipping remaining {n - 1} samples"
                    )
                    successful: list[tuple[dict, dict]] = []
                    if parsed0 is not None:
                        successful.append(r0)
                    return successful
        remaining = await asyncio.gather(*[
            _draft_one_outline(
                prompt, sample_idx=i, n_total=n, thread_id=thread_id,
            )
            for i in range(1, n)
        ])
        results.extend(remaining)
    else:
        results = await asyncio.gather(*[
            _draft_one_outline(
                prompt, sample_idx=i, n_total=n, thread_id=thread_id,
            )
            for i in range(n)
        ])
    successful: list[tuple[dict, dict]] = []
    for parsed, meta in results:
        if parsed is not None:
            successful.append((parsed, meta))
        else:
            logger.info(
                f"[outline_sdp] draft failed: {meta.get('error', 'unknown')}"
            )
    return successful


# =============================================================================
# USC vote
# =============================================================================
async def _usc_pick(
    candidates: list[tuple[ChapterOutline, OutlineDAG, list[str]]],
    chapter_id: str,
    chapter_title: str,
) -> int:
    """Run the USC picker over `candidates` (outline, dag, issues).
    Returns the chosen index. Falls back to 0 (first valid) on any
    picker failure."""
    if len(candidates) <= 1:
        return 0
    summaries = [
        summarize_candidate(o, d, issues)
        for (o, d, issues) in candidates
    ]
    prompt = build_usc_vote_prompt(
        candidates_summary=summaries,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
    )
    try:
        response, _ = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_VOTE,
            temperature=_TEMPERATURE_VOTE,
            response_format=_USC_VOTE_RESPONSE_FORMAT,
        )
        parsed = _parse_json_response(response)
        if parsed and "chosen_index" in parsed:
            idx = int(parsed["chosen_index"])
            if 0 <= idx < len(candidates):
                return idx
    except Exception as e:
        logger.warning(
            f"[outline_sdp] USC picker failed: "
            f"{type(e).__name__}: {e} — falling back to first candidate"
        )
    return 0


# =============================================================================
# Manifest hash + chapter lookup
# =============================================================================
def _compute_manifest_hash(
    *,
    sources: list[str],
    sources_bytes: int,
    chapter_title: str,
    chapter_description: str,
) -> str:
    payload = (
        f"sources={','.join(sorted(sources))}|"
        f"n_sources={len(sources)}|"
        f"bytes={sources_bytes}|"
        f"title={chapter_title}|"
        f"goal={chapter_description}|"
        f"prompt={OUTLINE_PROMPT_VERSION}|"
        f"schema={OUTLINE_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _find_chapter(plan: dict, chapter_id: str) -> Optional[dict]:
    """Look up a chapter by id in plan-latest.json. Returns None if
    not found."""
    chapters = (plan or {}).get("chapters") or []
    for ch in chapters:
        if isinstance(ch, dict) and ch.get("id") == chapter_id:
            return ch
    return None


# =============================================================================
# The node
# =============================================================================
@traced("outline_sdp")
async def outline_sdp(state: SynthState) -> dict:
    """Run the Structure-Driven Planner for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "no_slug_or_chapter_id",
                "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    # -- Load planner plan + locate chapter ---------------------------------
    plan_key = _planner_latest_key(slug)
    if not await minio.exists(plan_key):
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "plan_not_found",
                "plan_key": plan_key,
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"planner plan {plan_key!r} not in MinIO; run planner first",
        }

    plan_text = await minio.read_text(plan_key)
    try:
        plan = json.loads(plan_text)
    except Exception as e:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "plan_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"plan-latest.json unreadable: {type(e).__name__}: {e}",
        }

    chapter = _find_chapter(plan, chapter_id)
    if chapter is None:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped":     "chapter_not_in_plan",
                "wall_ms":     int((time.monotonic() - t0) * 1000),
                "known_ids":   [c.get("id") for c in (plan.get("chapters") or [])],
            },
            "status": "failed",
            "error":  f"chapter {chapter_id!r} not in plan-latest.json",
        }

    chapter_title       = chapter.get("title") or chapter_id
    chapter_description = chapter.get("description") or ""
    sources             = sorted(chapter.get("sources") or [])
    if not sources:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "no_sources",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"chapter {chapter_id!r} has zero sources in plan",
        }

    await emit_progress(
        thread_id, "outline_sdp", "start",
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        n_sources=len(sources),
    )

    # -- Read source bodies -------------------------------------------------
    # Each source is already corpus_normalized + vault_sentinelized by
    # ingestion (the 2026-05-19 architecture cleanup). We just concat.
    bodies = await minio.read_many(sources)
    bodies = [b for b in bodies if b]
    if not bodies:
        return {
            "outline_path":  "",
            "outline_stats": {
                "skipped": "source_bodies_empty",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  "all source bodies came back empty",
        }
    sources_concat_md, truncated = _concat_sources(bodies)
    n_vault_hashes = count_vault_sentinels(sources_concat_md)

    await emit_progress(
        thread_id, "outline_sdp", "sources_loaded",
        n_sources=len(sources),
        n_bodies=len(bodies),
        bytes=len(sources_concat_md),
        truncated=truncated,
        n_vault_hashes=n_vault_hashes,
    )

    # -- Cache fast-path ----------------------------------------------------
    manifest_hash = _compute_manifest_hash(
        sources=sources,
        sources_bytes=len(sources_concat_md),
        chapter_title=chapter_title,
        chapter_description=chapter_description,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            outline_dict = (cached or {}).get("outline") or {}
            dag_dict     = (cached or {}).get("dag") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_sections":   len(outline_dict.get("sections") or []),
                "n_challenges": len(outline_dict.get("challenges") or []),
                "n_flashcards": len(outline_dict.get("flashcards") or []),
                "max_stage":    int(dag_dict.get("max_stage", 0)),
                "n_stages":     len(dag_dict.get("stages") or {}),
                "n_removed_edges": len(dag_dict.get("removed_edges") or []),
                "wall_ms":      elapsed,
                "store_path":   latest_key,
                "versioned_path": versioned_key,
                "manifest_hash":  manifest_hash,
                "cache_hit":    True,
                "prompt_version": cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "outline_sdp", "done",
                n_sections=stats["n_sections"],
                max_stage=stats["max_stage"],
                wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[outline_sdp] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_sections']} sections, max_stage="
                f"{stats['max_stage']}, {elapsed} ms"
            )
            return {"outline_path": latest_key, "outline_stats": stats}
        except Exception as e:
            logger.warning(
                f"[outline_sdp] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # -- Build prompt + draft N samples -------------------------------------
    prompt = build_outline_prompt(
        framework=slug,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        chapter_description=chapter_description,
        n_vault_hashes=n_vault_hashes,
        sources_concat_md=sources_concat_md,
        target_sections_hint=_TARGET_SECTIONS_HINT,
    )
    raw_samples = await _generate_samples(
        prompt, _N_SAMPLES, thread_id, n_sources=len(sources),
    )

    await emit_progress(
        thread_id, "outline_sdp", "samples_drafted",
        n_samples=len(raw_samples), n_requested=_N_SAMPLES,
    )

    # -- Parse + Pydantic-validate each -------------------------------------
    candidates: list[tuple[ChapterOutline, OutlineDAG, list[str]]] = []
    pydantic_failures = 0
    for parsed_dict, meta in raw_samples:
        outline, err = _try_parse_outline(parsed_dict)
        if outline is None:
            pydantic_failures += 1
            logger.info(
                f"[outline_sdp] {slug}/{chapter_id}: pydantic-reject — {err}"
            )
            continue
        dag = derive_dag(outline.sections)
        _, issues = validate_outline_structure(
            outline, dag, n_sources=len(sources),
        )
        candidates.append((outline, dag, issues))

    await emit_progress(
        thread_id, "outline_sdp", "samples_validated",
        n_candidates=len(candidates), n_pydantic_fail=pydantic_failures,
    )

    # -- Fallback if NO candidates parsed -----------------------------------
    if not candidates:
        logger.warning(
            f"[outline_sdp] {slug}/{chapter_id}: ALL {_N_SAMPLES} samples "
            f"failed to parse; emitting heuristic fallback outline"
        )
        outline = _heuristic_fallback_outline(sources_concat_md)
        dag = derive_dag(outline.sections)
        candidates = [(outline, dag, ["heuristic_fallback"])]

    # -- USC pick best candidate --------------------------------------------
    chosen_idx = await _usc_pick(candidates, chapter_id, chapter_title)
    outline, dag, issues = candidates[chosen_idx]

    await emit_progress(
        thread_id, "outline_sdp", "usc_voted",
        chosen_index=chosen_idx, n_initial_violations=len(issues),
    )

    # -- Repair loop --------------------------------------------------------
    n_repairs = 0
    for attempt in range(_MAX_REPAIR_RETRIES):
        if not issues:
            break
        n_repairs += 1
        await emit_progress(
            thread_id, "outline_sdp", "repair_attempt",
            attempt=attempt + 1,
            n_violations=len(issues),
        )
        repair_prompt = build_repair_prompt(
            framework=slug,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            chapter_description=chapter_description,
            current_outline_json=json.dumps(outline.model_dump(), indent=2),
            issues=issues,
            sources_concat_md=sources_concat_md,
        )
        try:
            repair_response, _ = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
                response_format=_OUTLINE_RESPONSE_FORMAT,
            )
            parsed = _parse_json_response(repair_response)
            if not parsed:
                logger.warning(
                    f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                    f"{attempt + 1} produced unparseable JSON; keeping prior"
                )
                continue
            new_outline, err = _try_parse_outline(parsed)
            if new_outline is None:
                logger.warning(
                    f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                    f"{attempt + 1} pydantic-rejected: {err}"
                )
                continue
            new_dag = derive_dag(new_outline.sections)
            _, new_issues = validate_outline_structure(
                new_outline, new_dag, n_sources=len(sources),
            )
            # Only accept if it ACTUALLY improves things.
            if len(new_issues) <= len(issues):
                outline = new_outline
                dag = new_dag
                issues = new_issues
        except Exception as e:
            logger.warning(
                f"[outline_sdp] {slug}/{chapter_id}: repair attempt "
                f"{attempt + 1} failed: {type(e).__name__}: {e}"
            )
            continue

    # S1 (2026-05-26 late evening) — Hard-enforce outline section-count cap.
    #
    # CORR-3 Q1 emitted the adaptive-cap violation as a soft issue in
    # validate_outline_structure but Run 3 evidence showed the LLM
    # ignores it: BU ch-02 shipped 30 H2 (cap=12) after 3 repairs, CC
    # ch-01 shipped 20 H2 (cap=14). Soft pressure isn't enough.
    #
    # Programmatic trim: keep the first `cap` sections in topological
    # stage order (foundational concepts first). Prune prerequisite
    # references to dropped sections. Re-derive the DAG over the
    # trimmed set. This guarantees bounded-output regardless of LLM
    # compliance. Lost content was always defensible-by-fewer-than-3-
    # source-docs anyway (the cap is `n_sources // 3`).
    from .constants import _SECTIONS_MIN, max_h2_for_n_sources
    adaptive_cap = max(_SECTIONS_MIN, max_h2_for_n_sources(len(sources)))
    if len(outline.sections) > adaptive_cap:
        n_before = len(outline.sections)
        # Topological order: lower stage_index first.
        sections_by_stage: list[tuple[int, OutlineSection]] = []
        sid_to_section = {s.section_id: s for s in outline.sections}
        for stage_idx in sorted(dag.stages.keys()):
            for sid in dag.stages[stage_idx]:
                if sid in sid_to_section:
                    sections_by_stage.append((stage_idx, sid_to_section[sid]))
        # Sections not in any stage (orphans) appended last.
        seen = {s.section_id for _, s in sections_by_stage}
        for s in outline.sections:
            if s.section_id not in seen:
                sections_by_stage.append((dag.max_stage + 1, s))
        kept = [s for _, s in sections_by_stage[:adaptive_cap]]
        kept_ids = {s.section_id for s in kept}
        # Clean prereqs that point to dropped sections.
        for s in kept:
            s.prerequisites = [p for p in s.prerequisites if p in kept_ids]
        outline = outline.model_copy(update={"sections": kept})
        dag = derive_dag(outline.sections)
        logger.warning(
            f"[outline_sdp] {slug}/{chapter_id}: HARD-TRIM outline "
            f"{n_before} → {len(outline.sections)} sections (adaptive_cap="
            f"{adaptive_cap}, n_sources={len(sources)}). LLM ignored the "
            f"soft cap signal after {n_repairs} repairs; programmatic "
            f"trim restores the bound."
        )
        # Re-validate post-trim so downstream sees the actual remaining
        # violations (not the pre-trim ones, which may include the
        # cap-exceeded issue we just resolved).
        _, issues = validate_outline_structure(
            outline, dag, n_sources=len(sources),
        )
        await emit_progress(
            thread_id, "outline_sdp", "hard_trimmed",
            n_before=n_before, n_after=len(outline.sections),
            adaptive_cap=adaptive_cap, n_sources=len(sources),
        )

    final_violations = issues

    # -- Persist + return ---------------------------------------------------
    payload = _serialize_outline_with_dag(outline, dag)
    payload["framework_slug"]   = slug
    payload["chapter_id"]       = chapter_id
    payload["chapter_title"]    = chapter_title
    payload["manifest_hash"]    = manifest_hash
    payload["source_keys"]      = sources
    payload["n_vault_hashes"]   = n_vault_hashes
    payload["truncated"]        = truncated
    payload["n_repairs"]        = n_repairs
    payload["final_violations"] = final_violations

    blob_bytes = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, blob_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_sections":     len(outline.sections),
        "n_challenges":   len(outline.challenges),
        "n_flashcards":   len(outline.flashcards),
        "max_stage":      dag.max_stage,
        "n_stages":       len(dag.stages),
        "n_removed_edges": len(dag.removed_edges),
        "n_repairs":      n_repairs,
        "n_violations":   len(final_violations),
        "violations":     final_violations,
        "n_samples":      len(candidates),
        "n_vault_hashes": n_vault_hashes,
        "truncated":      truncated,
        "wall_ms":        elapsed,
        "store_path":     latest_key,
        "versioned_path": versioned_key,
        "manifest_hash":  manifest_hash,
        "cache_hit":      False,
        "prompt_version": OUTLINE_PROMPT_VERSION,
    }
    await emit_progress(
        thread_id, "outline_sdp", "done",
        n_sections=stats["n_sections"],
        max_stage=stats["max_stage"],
        n_repairs=n_repairs,
        n_violations=stats["n_violations"],
        wall_ms=elapsed,
    )
    logger.info(
        f"[outline_sdp] {slug}/{chapter_id}: {stats['n_sections']} "
        f"sections, max_stage={stats['max_stage']}, "
        f"n_stages={stats['n_stages']}, n_repairs={n_repairs}, "
        f"violations={len(final_violations)}, {elapsed} ms"
    )
    return {"outline_path": latest_key, "outline_stats": stats}


# =============================================================================
# Convenience loader for downstream nodes
# =============================================================================
def load_outline_payload(text: str) -> dict:
    """Parse the persisted outline blob. Returns the full payload dict;
    downstream nodes pick the fields they need (outline, dag, etc.)."""
    return json.loads(text)
