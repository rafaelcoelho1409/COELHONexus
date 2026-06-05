"""chapter_select I/O shell — persist the 4-blob output (select-specific +
reduce-compatible plan, each versioned + latest) + the chapter_select_run
orchestration."""
from __future__ import annotations

import json
import logging
import time

from ....ingestion.storage import get_storage
from ..chapter_assign import load_assignments
from ..chapter_propose import load_proposals
from ...runtime.progress import emit_progress
from ...state import PlannerState

from .domain import detect_pinned_indices, greedy_select, manifest_hash
from .keys import (
    chapter_plan_latest_key,
    chapter_plan_versioned_key,
    select_latest_key,
    select_versioned_key,
)
from .params import MIN_DOCS_PER_CHAPTER, MIN_KEPT_CHAPTERS
from .versions import PROMPT_VERSION


logger = logging.getLogger(__name__)


async def persist_select_outputs(
    minio,
    *,
    slug: str,
    manifest: str,
    select_payload: dict,
    plan_payload: dict,
) -> tuple[str, str]:
    """Write select + plan blobs (versioned + latest).
    Returns (plan_latest_key, plan_versioned_key)."""
    select_blob = json.dumps(
        select_payload, indent = 2, ensure_ascii = False,
    )
    plan_blob = json.dumps(plan_payload, indent = 2, ensure_ascii = False)

    vkey_select = select_versioned_key(slug, manifest)
    lkey_select = select_latest_key(slug)
    plan_vkey = chapter_plan_versioned_key(slug, manifest)
    plan_lkey = chapter_plan_latest_key(slug)

    await minio.write(
        vkey_select, select_blob, content_type = "application/json",
    )
    await minio.write(
        lkey_select, select_blob, content_type = "application/json",
    )
    await minio.write(
        plan_vkey, plan_blob, content_type = "application/json",
    )
    await minio.write(
        plan_lkey, plan_blob, content_type = "application/json",
    )

    return plan_lkey, plan_vkey


async def chapter_select_run(state: PlannerState) -> dict:
    """Pure-algorithm node: load → greedy-coverage (with pin override) →
    prune (<MIN_DOCS unless pinned) → re-assign survivors → persist."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    proposals_ref = state.get("chapter_proposals_ref")
    assignments_ref = state.get("chapter_doc_assignments_ref")

    if not slug or not proposals_ref or not assignments_ref:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "missing_inputs"},
        }

    t0 = time.monotonic()
    minio = get_storage()

    manifest = manifest_hash(
        slug = slug,
        proposals_ref = proposals_ref,
        assignments_ref = assignments_ref,
    )

    proposals_obj = await load_proposals(minio, slug)
    if proposals_obj is None or not proposals_obj.proposals:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "no_proposals"},
        }
    proposals = [p.model_dump() for p in proposals_obj.proposals]
    assignments = await load_assignments(minio, slug)
    if not assignments:
        return {
            "chapter_plan_ref": None,
            "select_stats": {"skipped": "no_assignments"},
        }

    seeds: dict = {}   # best-effort propose-side pin signal
    try:
        propose_text = await minio.read_text(proposals_ref)
        seeds = (json.loads(propose_text) or {}).get("seeds") or {}
    except Exception:
        pass

    pinned = detect_pinned_indices(proposals, seeds)

    await emit_progress(
        thread_id, "chapter_select", "start",
        n_proposals = len(proposals),
        n_docs = len(assignments),
        n_pinned = len(pinned),
    )

    selected, doc_to_chapter = greedy_select(
        proposals = proposals, assignments = assignments,
        pinned_indices = pinned,
    )

    docs_per_chapter: dict[int, list[str]] = {ci: [] for ci in selected}
    for k, ci in doc_to_chapter.items():
        if ci in docs_per_chapter:
            docs_per_chapter[ci].append(k)

    pruned: list[int] = []
    kept: list[int] = []
    for ci in selected:
        if len(docs_per_chapter.get(ci, [])) < MIN_DOCS_PER_CHAPTER:
            if ci in pinned:
                kept.append(ci)
            else:
                pruned.append(ci)
        else:
            kept.append(ci)

    # Restore lowest-pruned (by doc count) if too few chapters kept.
    if len(kept) < MIN_KEPT_CHAPTERS and pruned:
        pruned_sorted = sorted(
            pruned,
            key = lambda ci: len(docs_per_chapter.get(ci, [])),
            reverse = True,
        )
        while len(kept) < MIN_KEPT_CHAPTERS and pruned_sorted:
            ci = pruned_sorted.pop(0)
            kept.append(ci)
            pruned.remove(ci)

    # Reassign docs from pruned → next-best selected chapter.
    if pruned:
        kept_set = set(kept)
        for k, ci in list(doc_to_chapter.items()):
            if ci not in kept_set:
                scores = assignments.get(k) or []
                best_ci = None
                best_c = -1.0
                for s in scores:
                    si = s.get("chapter_idx")
                    sc = float(s.get("confidence") or 0.0)
                    if si in kept_set and sc > best_c:
                        best_c = sc
                        best_ci = si
                if best_ci is not None:
                    doc_to_chapter[k] = best_ci
                else:
                    del doc_to_chapter[k]
        docs_per_chapter = {ci: [] for ci in kept}
        for k, ci in doc_to_chapter.items():
            docs_per_chapter[ci].append(k)

    out_chapters: list[dict] = []   # reduce_node-compatible schema
    for order_idx, ci in enumerate(kept, 1):
        p = proposals[ci]
        out_chapters.append({
            "title":              p.get("title"),
            "description":        p.get("description"),
            "key_concepts":       p.get("key_concepts") or [],
            "member_doc_keys":    sorted(docs_per_chapter.get(ci, [])),
            "n_member_docs":      len(docs_per_chapter.get(ci, [])),
            "order":              order_idx,
            "source_proposal_idx": ci,
            "pinned":             ci in pinned,
        })

    n_assigned_docs = len(doc_to_chapter)
    n_total_docs = len(assignments)

    select_payload = {
        "prompt_version":     PROMPT_VERSION,
        "framework_slug":     slug,
        "manifest_hash":      manifest,
        "selected_indices":   kept,
        "pruned_indices":     pruned,
        "pinned_indices":     sorted(pinned),
        "n_proposals_in":     len(proposals),
        "n_chapters_out":     len(kept),
        "n_assigned_docs":    n_assigned_docs,
        "n_total_docs":       n_total_docs,
        "coverage_fraction":  (
            n_assigned_docs / n_total_docs if n_total_docs else 0.0
        ),
        "chapters":           out_chapters,
    }
    plan_payload = {
        "prompt_version":  PROMPT_VERSION,
        "framework_slug":  slug,
        "manifest_hash":   manifest,
        "outline": {
            "chapters": [
                {
                    "title":              c["title"],
                    "description":        c["description"],
                    "member_cluster_ids": [],   # n/a — LLM-first path
                    "member_doc_keys":    c["member_doc_keys"],
                    "order":              c["order"],
                }
                for c in out_chapters
            ],
        },
        "n_clusters_in":   len(proposals),
        "n_chapters_out":  len(kept),
        "n_repairs":       0,
        "forced_repair":   False,
        "source":          "llm_first_chapter_select_v1",
    }

    plan_lkey, _plan_vkey = await persist_select_outputs(
        minio,
        slug = slug,
        manifest = manifest,
        select_payload = select_payload,
        plan_payload = plan_payload,
    )

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_proposals_in":   len(proposals),
        "n_chapters_out":   len(kept),
        "n_pruned":         len(pruned),
        "n_assigned_docs":  n_assigned_docs,
        "n_total_docs":     n_total_docs,
        "coverage_fraction": (
            n_assigned_docs / n_total_docs if n_total_docs else 0.0
        ),
        "wall_ms":          wall_ms,
        "manifest_hash":    manifest,
        "chapter_titles":   [c["title"] for c in out_chapters],
        "chapter_sizes":    [c["n_member_docs"] for c in out_chapters],
    }
    await emit_progress(
        thread_id, "chapter_select", "done",
        n_chapters = len(kept), n_pruned = len(pruned),
        coverage = stats["coverage_fraction"],
        wall_ms = wall_ms,
        titles = stats["chapter_titles"],
    )
    return {"chapter_plan_ref": plan_lkey, "select_stats": stats}
