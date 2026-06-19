"""chapter_assign I/O shell — per-doc LLM scoring, latest-blob loader,
and the chapter_assign_run orchestration."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ..chapter_propose import load_proposals
from ..doc_distill import load_distillates
from ...runtime.progress import emit_progress
from ...state import PlannerState

from .domain import fallback_assign_scores, manifest_hash, parse
from .keys import latest_key, versioned_key
from .params import (
    CONCURRENCY,
    CONFIDENCE_THRESHOLD,
    MAX_TOKENS,
    RESCUE_FLOOR,
    TEMPERATURE,
)
from .prompts import build_prompt
from .schemas import ASSIGN_RESPONSE_FORMAT, DocAssignment
from .versions import PROMPT_VERSION


logger = logging.getLogger(__name__)


async def assign_one(
    sem: asyncio.Semaphore,
    minio,
    framework: str,
    source_key: str,
    distillate: Optional[dict],
    proposals: list[dict],
) -> tuple[str, Optional[list[dict]], int, bool]:
    """(source_key, scores, wall_ms, used_fallback). scores=None only when
    doc has no content; failed LLM with content gets lexical fallback."""
    async with sem:
        t0 = time.monotonic()
        doc_summary = (distillate or {}).get("summary") or ""
        doc_terms = (distillate or {}).get("key_terms") or []
        doc_body = ""
        if not doc_summary:
            try:
                doc_body = await minio.read_text(source_key)
            except Exception:
                pass
            if not doc_body:
                return (
                    source_key, None,
                    int((time.monotonic() - t0) * 1000),
                    False,
                )

        prompt = build_prompt(
            framework = framework,
            source_key = source_key,
            doc_summary = doc_summary,
            doc_terms = doc_terms,
            doc_body = doc_body,
            proposals = proposals,
        )

        scores: Optional[list[dict]] = None
        try:
            # dd-reduce-label = non-reasoning pool; <think> blocks waste 10-25s on JSON scoring.
            raw, _ = await chat_judge_bandit_async(
                prompt,
                max_tokens = MAX_TOKENS,
                temperature = TEMPERATURE,
                response_format = ASSIGN_RESPONSE_FORMAT,
                dd_process = "dd-reduce-label",
            )
            parsed = parse(raw)
            if parsed:
                assignment = DocAssignment.model_validate(parsed)
                n_proposals = len(proposals)
                scores = [
                    {
                        "chapter_idx": s.chapter_idx,
                        "confidence":  s.confidence,
                    }
                    for s in assignment.scores
                    if 0 <= s.chapter_idx < n_proposals
                ]
        except Exception as e:
            logger.warning(
                f"[chapter_assign] LLM/parse/validate failed for "
                f"{source_key}: {type(e).__name__}: {e}"
            )

        # Failed LLM (None) → lexical fallback so doc reaches chapter_select.
        # Successful but empty (LLM judged irrelevant) is left as-is.
        used_fallback = False
        if scores is None:
            scores = fallback_assign_scores(
                doc_summary, doc_terms, proposals,
            )
            used_fallback = bool(scores)
            if used_fallback:
                logger.info(
                    f"[chapter_assign] {source_key}: assign failed — "
                    f"lexical fallback → chapter "
                    f"{scores[0]['chapter_idx']} (doc kept)"
                )
        return (
            source_key, scores,
            int((time.monotonic() - t0) * 1000),
            used_fallback,
        )


async def load_assignments(minio, slug: str) -> dict:
    """Returns {source_key: [{chapter_idx, confidence}, ...]}."""
    try:
        text = await minio.read_text(latest_key(slug))
        data = json.loads(text)
        return data.get("assignments") or {}
    except Exception:
        return {}


async def chapter_assign_run(state: PlannerState) -> dict:
    """Load proposals + distillates → score every doc via the bandit →
    persist {source_key: [{chapter_idx, confidence}]} matrix."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    relevant_files = (
        state.get("relevant_files") or state.get("raw_files") or []
    )
    proposals_ref = state.get("chapter_proposals_ref")

    if not slug or not relevant_files or not proposals_ref:
        return {
            "chapter_doc_assignments_ref": None,
            "assign_stats": {"skipped": "missing_inputs"},
        }

    t0 = time.monotonic()
    minio = get_storage()
    proposals_obj = await load_proposals(minio, slug)
    if proposals_obj is None or not proposals_obj.proposals:
        return {
            "chapter_doc_assignments_ref": None,
            "assign_stats": {"skipped": "no_proposals_loaded"},
        }
    proposals_dicts = [p.model_dump() for p in proposals_obj.proposals]
    distillates = await load_distillates(minio, slug)

    manifest = manifest_hash(
        slug = slug,
        proposals_ref = proposals_ref,
        source_keys = relevant_files,
    )
    vkey = versioned_key(slug, manifest)
    lkey = latest_key(slug)
    if await minio.exists(vkey) and await minio.exists(lkey):
        try:
            cached = json.loads(await minio.read_text(vkey))
            wall_ms = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_docs": len(cached.get("assignments") or {}),
                "n_proposals": len(proposals_dicts),
                "cache_hit": True,
                "wall_ms": wall_ms,
                "manifest_hash": manifest,
            }
            await emit_progress(
                thread_id, "chapter_assign", "done",
                cache_hit = True,
                n_docs = stats["n_docs"],
                wall_ms = wall_ms,
            )
            return {
                "chapter_doc_assignments_ref": lkey,
                "assign_stats": stats,
            }
        except Exception:
            pass

    await emit_progress(
        thread_id, "chapter_assign", "start",
        n_docs = len(relevant_files),
        n_proposals = len(proposals_dicts),
    )

    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        assign_one(
            sem, minio, slug, k,
            distillates.get(k), proposals_dicts,
        )
        for k in relevant_files
    ]
    results = await asyncio.gather(*tasks, return_exceptions = False)

    assignments: dict[str, list[dict]] = {}
    n_failed = 0
    fallbacks: list[str] = []
    for k, scores, _wall, used_fb in results:
        if scores is None:
            n_failed += 1
            continue
        assignments[k] = scores
        if used_fb:
            fallbacks.append(k)

    # v3 (2026-06-05) — RESCUE PASS. Sub-threshold-but-above-floor docs
    # get their best score floored to CONFIDENCE_THRESHOLD so they enter
    # greedy_select's `assignable` set. Without this, the [0.3, 0.5)
    # confidence band silently drops genuine content (~18 docs on CC).
    # Done in-place on the assignments dict so the persisted blob
    # reflects the post-rescue scoring; the original LLM scores are
    # still visible in the per-doc score list (only the BEST score is
    # nudged).
    rescued: list[dict] = []
    for k, scores in assignments.items():
        if not scores:
            continue
        # Find the best (chapter_idx, confidence) tuple.
        best_idx = 0
        best_conf = float(scores[0].get("confidence") or 0.0)
        for i, s in enumerate(scores[1:], 1):
            c = float(s.get("confidence") or 0.0)
            if c > best_conf:
                best_conf = c
                best_idx = i
        if best_conf < CONFIDENCE_THRESHOLD and best_conf >= RESCUE_FLOOR:
            original = best_conf
            scores[best_idx]["confidence"] = CONFIDENCE_THRESHOLD
            scores[best_idx]["rescued_from"] = original
            rescued.append({
                "key":           k,
                "chapter_idx":   scores[best_idx]["chapter_idx"],
                "original_conf": original,
            })

    if rescued:
        logger.warning(
            f"[chapter_assign] {slug}: rescued {len(rescued)} doc(s) "
            f"from the [{RESCUE_FLOOR}, {CONFIDENCE_THRESHOLD}) confidence "
            f"band — floored best score to {CONFIDENCE_THRESHOLD} so they "
            f"reach a chapter instead of being silently dropped at "
            f"chapter_select. Sample: "
            f"{[(r['key'], r['original_conf']) for r in rescued[:10]]}"
        )
        try:
            from infra.langfuse.annotation import flag_for_review
            flag_for_review(
                f"chapter_assign rescued {len(rescued)} doc(s) in confidence "
                f"band [{RESCUE_FLOOR}, {CONFIDENCE_THRESHOLD})",
                severity = "low",
            )
        except Exception:
            pass

    # Rebuild coverage_count AFTER rescue so the post-rescue picture is
    # what flows into the stats payload.
    coverage_count: dict[int, int] = {
        i: 0 for i in range(len(proposals_dicts))
    }
    for k, scores in assignments.items():
        for s in scores:
            if s["confidence"] >= CONFIDENCE_THRESHOLD:
                coverage_count[s["chapter_idx"]] = coverage_count.get(
                    s["chapter_idx"], 0,
                ) + 1

    if fallbacks:
        logger.warning(
            f"[chapter_assign] {slug}: {len(fallbacks)} doc(s) used a "
            f"lexical fallback assignment (assign LLM failed but doc "
            f"kept): {fallbacks[:20]}"
        )

    payload = {
        "prompt_version":     PROMPT_VERSION,
        "framework_slug":     slug,
        "manifest_hash":      manifest,
        "assignments":        assignments,
        "n_docs":             len(relevant_files),
        "n_assigned":         len(assignments),
        "n_failed":           n_failed,
        "n_fallback":         len(fallbacks),
        "fallbacks":          fallbacks[:20],
        "n_rescued":          len(rescued),
        "rescued":            rescued[:20],
        "n_proposals":        len(proposals_dicts),
        "coverage_count":     coverage_count,
        "confidence_thresh":  CONFIDENCE_THRESHOLD,
        "rescue_floor":       RESCUE_FLOOR,
    }
    blob = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(vkey, blob, content_type = "application/json")
    await minio.write(lkey, blob, content_type = "application/json")

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_docs": len(relevant_files),
        "n_assigned": len(assignments),
        "n_failed": n_failed,
        "n_fallback": len(fallbacks),
        "n_rescued": len(rescued),
        "n_proposals": len(proposals_dicts),
        "coverage_count": coverage_count,
        "cache_hit": False,
        "wall_ms": wall_ms,
        "manifest_hash": manifest,
    }
    await emit_progress(
        thread_id, "chapter_assign", "done",
        cache_hit = False,
        n_assigned = len(assignments),
        n_failed = n_failed,
        n_fallback = len(fallbacks),
        n_rescued = len(rescued),
        wall_ms = wall_ms,
    )
    return {
        "chapter_doc_assignments_ref": lkey,
        "assign_stats": stats,
    }
