"""Substep 8 — reduce: merge N labeled clusters into a 4-12 chapter outline.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA research
(TnT-LLM arXiv 2403.12173 + TopicGPT NAACL 2024 + Universal Self-
Consistency arXiv 2311.17311 + Self-Refine Madaan 2023 + Nature npj-AI
2025 plateau finding). Pipeline:

  1. Load cluster + refine + label artifacts.
  2. For each cluster, build context: label + size + top-5 c-TF-IDF
     keywords + 1 rep-doc first-line.
  3. SINGLE LLM call (NOT iterative pairwise — only pays off at N≥40
     per LLM-Assisted Topic Reduction ECML PKDD 2025). N=3 samples at
     temp=0.3, JSON output.
  4. Universal Self-Consistency vote — one extra LLM call picks the
     best of 3 outlines by coverage + coherence rubric.
  5. ONE self-refine pass (Madaan 2023 FEEDBACK→REFINE). Nature 2025
     npj-AI shows 2 rounds plateau for structured tasks; we use 1.
  6. Coverage post-validate — set-equality on member_cluster_ids vs
     input. Up to 3 repair retries (TnT-LLM reports 12% silent-drop
     rate on raw outputs). Last-resort force-repair dumps orphans
     into Miscellaneous.
  7. Persist as MinIO JSON.

Schema enforced post-parse (same JSON-extract pattern as refine/label —
no `instructor` dep needed):

  chapters: list[{
      title:               str (2-6 words, Title Case noun phrase)
      description:         str (1 sentence)
      member_cluster_ids:  list[int]
      order:               int (1-based)
  }]
  assigned_cluster_ids: list[int]   # TnT-LLM mirror for coverage check

State writes:
  chapter_plan_ref — MinIO key of the JSON blob
  reduce_stats     — observability dict (counts + full outline for UI)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from hashlib import sha256

import numpy as np

from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import chat_judge_bandit_async

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from ..cluster import load_clusters
from ..label import load_labels
from ..refine import _compute_cluster_keywords, load_refine

from .constants import (
    _BLOB_PREFIX,
    _CTFIDF_DOC_CHARS,
    _K_MAX,
    _K_MIN,
    _KEYWORDS_PER_CLUSTER,
    _MAX_REPAIR_RETRIES,
    _MAX_TOKENS_OUTLINE,
    _MAX_TOKENS_REFINE,
    _MAX_TOKENS_REPAIR,
    _MAX_TOKENS_VOTE,
    _MISC_CHAPTER_TITLE,
    _N_SAMPLES,
    _PROMPT_VERSION,
    _REP_DOC_CHARS,
    _TARGET_K,
    _TEMPERATURE,
)
from .service import (
    _blob_key,
    _build_cluster_context_block,
    _build_coverage_repair_prompt,
    _build_reduce_prompt,
    _build_refine_apply_prompt,
    _build_refine_feedback_prompt,
    _build_usc_vote_prompt,
    _force_coverage_fallback,
    _generate_one_outline,
    _parse_response,
    _pick_rep_first_line,
    _validate_outline,
)


logger = logging.getLogger(__name__)


@traced("reduce")
async def reduce_node(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    cluster_ref = state.get("cluster_assignments_ref") or ""
    refine_ref = state.get("refine_assignments_ref") or ""
    labels_ref = state.get("cluster_labels_ref") or ""
    if not slug or not cluster_ref or not refine_ref or not labels_ref:
        return {
            "chapter_plan_ref": "",
            "reduce_stats": {"skipped": "no_input", "wall_ms": 0,
                             "n_chapters": 0},
        }

    t0 = time.monotonic()

    # ── Cache fast-path ────────────────────────────────────────────────
    mh = sha256(
        (f"cluster={cluster_ref}|refine={refine_ref}|"
         f"labels={labels_ref}|v={_PROMPT_VERSION}|"
         f"k={_TARGET_K}|min={_K_MIN}|max={_K_MAX}|"
         f"n={_N_SAMPLES}").encode("utf-8"),
    ).hexdigest()[:16]
    blob_key = _blob_key(slug, mh)
    minio = get_storage()

    if await minio.exists(blob_key):
        try:
            blob = await minio.read_text(blob_key)
            cached = json.loads(blob)
            outline = cached.get("outline") or {}
            chapters = outline.get("chapters") or []
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_chapters":     len(chapters),
                "n_clusters_in":  cached.get("n_clusters_in", 0),
                "n_repairs":      cached.get("n_repairs", 0),
                "wall_ms":        elapsed,
                "store_path":     blob_key,
                "cache_hit":      True,
                "outline":        outline,
                "prompt_version": cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "reduce", "done",
                n_chapters=len(chapters), wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[reduce] {slug}: CACHE HIT — {len(chapters)} chapters, "
                f"{elapsed} ms"
            )
            return {"chapter_plan_ref": blob_key, "reduce_stats": stats}
        except Exception as e:
            logger.warning(
                f"[reduce] {slug}: cached blob {blob_key!r} unreadable "
                f"({type(e).__name__}: {e}); recomputing"
            )

    await emit_progress(thread_id, "reduce", "start")

    # ── Load upstream artifacts ────────────────────────────────────────
    cluster_blob = await minio.read_bytes(cluster_ref)
    cluster_keys, _orig, _max_probs, soft = load_clusters(cluster_blob)
    refine_blob = await minio.read_bytes(refine_ref)
    _, refined_assignments, _, _ = load_refine(refine_blob)
    labels_text = await minio.read_text(labels_ref)
    labels = load_labels(labels_text)

    bodies = await minio.read_many(cluster_keys)
    unique_clusters = sorted({
        int(cid) for cid in refined_assignments if int(cid) >= 0
    })
    n_clusters_in = len(unique_clusters)

    if n_clusters_in == 0:
        elapsed = int((time.monotonic() - t0) * 1000)
        outline: dict = {
            "chapters": [{
                "title": _MISC_CHAPTER_TITLE,
                "description": "Corpus did not yield any topical clusters.",
                "member_cluster_ids": [],
                "order": 1,
            }],
            "assigned_cluster_ids": [],
        }
        payload = {
            "outline":        outline,
            "n_clusters_in":  0,
            "n_repairs":      0,
            "prompt_version": _PROMPT_VERSION,
        }
        await minio.write(
            blob_key, json.dumps(payload), content_type="application/json",
        )
        stats = {
            "n_chapters": 1, "n_clusters_in": 0, "n_repairs": 0,
            "wall_ms": elapsed, "store_path": blob_key, "cache_hit": False,
            "outline": outline, "skipped": "no_clusters",
        }
        await emit_progress(
            thread_id, "reduce", "done",
            n_chapters=1, wall_ms=elapsed,
        )
        return {"chapter_plan_ref": blob_key, "reduce_stats": stats}

    # ── Per-cluster context (label + size + keywords + rep first line) ─
    cluster_docs_text: dict[int, str] = {}
    cluster_sizes: dict[int, int] = {}
    for cid in unique_clusters:
        cluster_mask_c = refined_assignments == cid
        idxs = np.where(cluster_mask_c)[0]
        cluster_sizes[cid] = int(len(idxs))
        if len(idxs):
            cluster_docs_text[cid] = " ".join(
                (bodies[int(i)] or "")[:_CTFIDF_DOC_CHARS] for i in idxs
            )
    cluster_keywords = _compute_cluster_keywords(
        cluster_docs_text, top_k=_KEYWORDS_PER_CLUSTER,
    )
    cluster_rep_lines = {
        cid: _pick_rep_first_line(cid, refined_assignments, soft, bodies)
        for cid in unique_clusters
    }
    cluster_blocks = [
        _build_cluster_context_block(
            cid,
            labels.get(cid, f"Cluster {cid}"),
            cluster_sizes.get(cid, 0),
            cluster_keywords.get(cid, []),
            cluster_rep_lines.get(cid, ""),
        )
        for cid in unique_clusters
    ]
    input_cluster_ids = set(unique_clusters)

    await emit_progress(
        thread_id, "reduce", "context_prepared",
        n_clusters_in=n_clusters_in,
    )

    # ── Generate N samples in parallel ─────────────────────────────────
    prompt = _build_reduce_prompt(cluster_blocks, _TARGET_K)
    sample_results = await asyncio.gather(*[
        _generate_one_outline(prompt) for _ in range(_N_SAMPLES)
    ])
    valid_samples = [
        (s, m) for s, m in sample_results if s is not None
    ]
    if not valid_samples:
        elapsed = int((time.monotonic() - t0) * 1000)
        outline = _force_coverage_fallback(
            {"chapters": []}, list(input_cluster_ids), [], [],
        )
        payload = {
            "outline": outline, "n_clusters_in": n_clusters_in,
            "n_repairs": 0, "prompt_version": _PROMPT_VERSION,
            "error": "all_samples_failed",
        }
        await minio.write(
            blob_key, json.dumps(payload), content_type="application/json",
        )
        stats = {
            "n_chapters": len(outline["chapters"]),
            "n_clusters_in": n_clusters_in, "n_repairs": 0,
            "wall_ms": elapsed, "store_path": blob_key, "cache_hit": False,
            "outline": outline, "error": "all_samples_failed",
        }
        await emit_progress(
            thread_id, "reduce", "done",
            n_chapters=len(outline["chapters"]), wall_ms=elapsed,
            error="all_samples_failed",
        )
        logger.warning(
            f"[reduce] {slug}: all {_N_SAMPLES} samples failed; "
            f"emitted fallback outline"
        )
        return {"chapter_plan_ref": blob_key, "reduce_stats": stats}

    await emit_progress(
        thread_id, "reduce", "samples_generated",
        n_samples=len(valid_samples),
    )

    # ── USC vote: pick best sample ─────────────────────────────────────
    chosen_sample = valid_samples[0][0]
    if len(valid_samples) > 1:
        vote_prompt = _build_usc_vote_prompt(
            [s for s, _ in valid_samples], input_cluster_ids,
        )
        try:
            vote_response, _ = await chat_judge_bandit_async(
                vote_prompt, max_tokens=_MAX_TOKENS_VOTE, temperature=0.0,
            )
            vote_parsed = _parse_response(vote_response)
            if vote_parsed and "chosen_index" in vote_parsed:
                idx = int(vote_parsed["chosen_index"])
                if 0 <= idx < len(valid_samples):
                    chosen_sample = valid_samples[idx][0]
        except Exception:
            pass

    await emit_progress(thread_id, "reduce", "usc_voted")

    # ── Self-refine pass (single round per Nature 2025 plateau) ────────
    feedback_prompt = _build_refine_feedback_prompt(
        chosen_sample, input_cluster_ids,
    )
    refined_outline = chosen_sample
    try:
        feedback_text, _ = await chat_judge_bandit_async(
            feedback_prompt, max_tokens=_MAX_TOKENS_VOTE, temperature=0.0,
        )
        if (
            feedback_text
            and "no issues" not in feedback_text.lower()
        ):
            apply_prompt = _build_refine_apply_prompt(
                chosen_sample, feedback_text, cluster_blocks, _TARGET_K,
            )
            apply_text, _ = await chat_judge_bandit_async(
                apply_prompt, max_tokens=_MAX_TOKENS_REFINE,
                temperature=_TEMPERATURE,
            )
            apply_parsed = _parse_response(apply_text)
            if apply_parsed and apply_parsed.get("chapters"):
                refined_outline = apply_parsed
    except Exception as e:
        logger.warning(
            f"[reduce] {slug}: self-refine pass failed "
            f"({type(e).__name__}: {e}); using USC winner as-is"
        )

    await emit_progress(thread_id, "reduce", "refined")

    # ── Coverage validation + repair retries ───────────────────────────
    n_repairs = 0
    for attempt in range(_MAX_REPAIR_RETRIES):
        missing, dupes, unknown = _validate_outline(
            refined_outline, input_cluster_ids,
        )
        if not (missing or dupes or unknown):
            break
        n_repairs += 1
        await emit_progress(
            thread_id, "reduce", "repair_attempt",
            attempt=attempt + 1, missing=len(missing),
            duplicate=len(dupes), unknown=len(unknown),
        )
        repair_prompt = _build_coverage_repair_prompt(
            refined_outline, missing, dupes, unknown, cluster_blocks,
        )
        try:
            repair_text, _ = await chat_judge_bandit_async(
                repair_prompt, max_tokens=_MAX_TOKENS_REPAIR,
                temperature=0.0,
            )
            repair_parsed = _parse_response(repair_text)
            if repair_parsed and repair_parsed.get("chapters"):
                refined_outline = repair_parsed
        except Exception as e:
            logger.warning(
                f"[reduce] {slug}: repair attempt {attempt + 1} failed "
                f"({type(e).__name__}: {e})"
            )
            break

    # Final fallback: if still incomplete after retries, force-repair.
    missing, dupes, unknown = _validate_outline(
        refined_outline, input_cluster_ids,
    )
    forced_repair = False
    if missing or dupes or unknown:
        refined_outline = _force_coverage_fallback(
            refined_outline, missing, dupes, unknown,
        )
        forced_repair = True
        logger.warning(
            f"[reduce] {slug}: coverage incomplete after "
            f"{_MAX_REPAIR_RETRIES} retries; force-repair applied "
            f"(missing={len(missing)}, dup={len(dupes)}, "
            f"unknown={len(unknown)})"
        )

    # ── Persist + return ───────────────────────────────────────────────
    chapters = refined_outline.get("chapters") or []
    payload = {
        "outline":        refined_outline,
        "n_clusters_in":  n_clusters_in,
        "n_repairs":      n_repairs,
        "forced_repair":  forced_repair,
        "prompt_version": _PROMPT_VERSION,
    }
    await minio.write(
        blob_key, json.dumps(payload), content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_chapters":     len(chapters),
        "n_clusters_in":  n_clusters_in,
        "n_samples":      len(valid_samples),
        "n_repairs":      n_repairs,
        "forced_repair":  forced_repair,
        "wall_ms":        elapsed,
        "store_path":     blob_key,
        "cache_hit":      False,
        "outline":        refined_outline,
        "prompt_version": _PROMPT_VERSION,
    }
    await emit_progress(
        thread_id, "reduce", "done",
        n_chapters=len(chapters), n_repairs=n_repairs,
        forced_repair=forced_repair, wall_ms=elapsed,
    )
    logger.info(
        f"[reduce] {slug}: {len(chapters)} chapters from {n_clusters_in} "
        f"clusters; {n_repairs} repairs"
        f"{' (forced)' if forced_repair else ''}; {elapsed} ms"
    )
    return {"chapter_plan_ref": blob_key, "reduce_stats": stats}
