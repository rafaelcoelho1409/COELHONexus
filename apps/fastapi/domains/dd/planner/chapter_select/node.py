"""chapter_select — greedy coverage selection over proposed chapters.

Algorithm (pure Python, no LLM):
  1. Load proposals + assignments.
  2. Build a doc × chapter confidence matrix (mask below τ_confidence=0.5).
  3. Greedy:
     - Identify structurally-seeded chapters (currently pin-via-seeds is
       opt-in via metadata; default empty set). Pinned chapters always
       included.
     - While uncovered docs exist AND chapters remain:
         pick chapter maximizing sum-of-confidences over uncovered docs.
         add it; mark covered docs (confidence >= τ) as covered.
     - Stop when ≥95% of assignable docs are covered OR no remaining
       chapter would cover any new doc.
  4. Prune chapters with <3 assigned docs unless pinned.
  5. Re-assign each doc to the chapter where it scored highest among
     SELECTED chapters (single-assignment for downstream Synth).
  6. Output schema matches legacy reduce_node: {outline: {chapters: [...]}}

State writes:
  chapter_plan_ref — MinIO key of the JSON (same field as legacy reduce)
  select_stats     — counts, coverage, pruned, etc.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from hashlib import sha256
from typing import Optional

from ...ingestion.storage import get_storage

from ..chapter_assign import load_assignments
from ..chapter_propose import load_proposals
from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState


logger = logging.getLogger(__name__)


_BLOB_PREFIX = "planner"
_PROMPT_VERSION = "v1-2026-05-27"   # no LLM; bump invalidates cache

# Greedy coverage tuning.
_CONFIDENCE_THRESHOLD = 0.5
_COVERAGE_TARGET      = 0.95
_MIN_DOCS_PER_CHAPTER = 3   # prune chapters below this unless pinned


def _greedy_select(
    *,
    proposals: list[dict],
    assignments: dict[str, list[dict]],
    pinned_indices: set[int],
) -> tuple[list[int], dict[str, int]]:
    """Run greedy coverage selection.

    Returns:
      selected_indices — list of proposal indices in selection order
      doc_to_chapter   — for each assigned doc, the picked chapter idx
                         (highest confidence among SELECTED chapters)
    """
    n_proposals = len(proposals)
    # Build per-doc confidence vector keyed by chapter index.
    doc_confidences: dict[str, dict[int, float]] = {}
    for k, scores in assignments.items():
        cv: dict[int, float] = {}
        for s in scores:
            ci = s.get("chapter_idx")
            cv[ci] = float(s.get("confidence") or 0.0)
        doc_confidences[k] = cv

    # Docs that have at least one above-threshold score are "assignable".
    assignable = {
        k for k, cv in doc_confidences.items()
        if any(c >= _CONFIDENCE_THRESHOLD for c in cv.values())
    }
    n_assignable = len(assignable)
    if n_assignable == 0:
        return list(range(n_proposals)), {}

    covered: set[str] = set()
    selected: list[int] = []
    selected_set: set[int] = set()

    # Force-include pinned chapters first.
    for ci in sorted(pinned_indices):
        if 0 <= ci < n_proposals:
            selected.append(ci)
            selected_set.add(ci)
            for k in assignable:
                if doc_confidences[k].get(ci, 0.0) >= _CONFIDENCE_THRESHOLD:
                    covered.add(k)

    # Greedy: pick chapter maximizing sum of confidences over uncovered docs.
    coverage = len(covered) / n_assignable if n_assignable else 1.0
    while coverage < _COVERAGE_TARGET and len(selected) < n_proposals:
        best_idx = -1
        best_gain = 0.0
        for ci in range(n_proposals):
            if ci in selected_set:
                continue
            gain = 0.0
            for k in assignable:
                if k in covered:
                    continue
                c = doc_confidences[k].get(ci, 0.0)
                if c >= _CONFIDENCE_THRESHOLD:
                    gain += c
            if gain > best_gain:
                best_gain = gain
                best_idx = ci
        if best_idx < 0 or best_gain <= 0.0:
            break
        selected.append(best_idx)
        selected_set.add(best_idx)
        for k in assignable:
            if doc_confidences[k].get(best_idx, 0.0) >= _CONFIDENCE_THRESHOLD:
                covered.add(k)
        coverage = len(covered) / n_assignable if n_assignable else 1.0

    # Assign each doc to ONE selected chapter (highest confidence). Allows
    # docs that ALSO score above threshold elsewhere to land somewhere
    # sensible. Docs with no above-τ score in any selected chapter are
    # assigned to the SINGLE selected chapter where they have the highest
    # (sub-threshold) confidence — preserves the per-doc lineage even when
    # confidence is weak (single-assignment is what downstream Synth needs).
    doc_to_chapter: dict[str, int] = {}
    for k in assignments.keys():
        cv = doc_confidences.get(k) or {}
        # restrict to selected indices
        sel_scores = [(ci, cv.get(ci, 0.0)) for ci in selected_set]
        if not sel_scores:
            continue
        sel_scores.sort(key=lambda p: p[1], reverse=True)
        best_ci, best_c = sel_scores[0]
        if best_c <= 0.0 and k not in assignable:
            # Doc had no signal anywhere; skip assignment.
            continue
        doc_to_chapter[k] = best_ci

    return selected, doc_to_chapter


def _detect_pinned_indices(proposals: list[dict], seeds: dict) -> set[int]:
    """Mark proposals as PINNED when their title or key_concepts overlap
    strongly with a structural namespace. Currently empty by default —
    namespace pinning is opt-in via future work."""
    # Conservative: no auto-pinning in v1. Structural seeds influence
    # propose-time prompting; pinning here would risk locking in weak
    # proposals. Reserved for future tuning.
    return set()


def _manifest_hash(
    *, slug: str, proposals_ref: str, assignments_ref: str,
) -> str:
    h = sha256()
    h.update(_PROMPT_VERSION.encode())
    h.update(slug.encode())
    h.update(b"|"); h.update(proposals_ref.encode())
    h.update(b"|"); h.update(assignments_ref.encode())
    return h.hexdigest()[:16]


def _versioned_key(slug: str, manifest: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapter_select/{manifest}.json"


def _latest_key(slug: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapter_select-latest.json"


def _chapter_plan_versioned_key(slug: str, manifest: str) -> str:
    """Write to the SAME blob layout as reduce_node so order_chapters +
    plan_write read it transparently."""
    return f"{_BLOB_PREFIX}/{slug}/chapters/{manifest}.json"


def _chapter_plan_latest_key(slug: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapter_plan-latest.json"


@traced("chapter_select")
async def chapter_select(state: PlannerState) -> dict:
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

    manifest = _manifest_hash(
        slug=slug, proposals_ref=proposals_ref, assignments_ref=assignments_ref,
    )
    vkey_select = _versioned_key(slug, manifest)
    lkey_select = _latest_key(slug)

    # Load proposals + assignments + propose-side seeds (for any pin signal).
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

    # Load seeds from chapter_propose blob (best-effort).
    seeds: dict = {}
    try:
        propose_text = await minio.read_text(proposals_ref)
        seeds = (json.loads(propose_text) or {}).get("seeds") or {}
    except Exception:
        pass

    pinned = _detect_pinned_indices(proposals, seeds)

    await emit_progress(
        thread_id, "chapter_select", "start",
        n_proposals=len(proposals), n_docs=len(assignments),
        n_pinned=len(pinned),
    )

    # Run greedy coverage.
    selected, doc_to_chapter = _greedy_select(
        proposals=proposals, assignments=assignments,
        pinned_indices=pinned,
    )

    # Compute doc lists per selected chapter.
    docs_per_chapter: dict[int, list[str]] = {ci: [] for ci in selected}
    for k, ci in doc_to_chapter.items():
        if ci in docs_per_chapter:
            docs_per_chapter[ci].append(k)

    # Prune chapters with <_MIN_DOCS_PER_CHAPTER docs (unless pinned).
    pruned: list[int] = []
    kept: list[int] = []
    for ci in selected:
        if len(docs_per_chapter.get(ci, [])) < _MIN_DOCS_PER_CHAPTER:
            if ci in pinned:
                kept.append(ci)
            else:
                pruned.append(ci)
        else:
            kept.append(ci)

    # If pruning leaves <_MIN_CHAPTERS chapters, restore lowest-pruned by doc count.
    _MIN_KEPT_CHAPTERS = 3
    if len(kept) < _MIN_KEPT_CHAPTERS and pruned:
        # Sort pruned by doc count desc, restore best until floor met.
        pruned_sorted = sorted(
            pruned, key=lambda ci: len(docs_per_chapter.get(ci, [])), reverse=True,
        )
        while len(kept) < _MIN_KEPT_CHAPTERS and pruned_sorted:
            ci = pruned_sorted.pop(0)
            kept.append(ci)
            pruned.remove(ci)

    # Reassign docs from pruned chapters to their NEXT-best selected chapter.
    if pruned:
        kept_set = set(kept)
        for k, ci in list(doc_to_chapter.items()):
            if ci not in kept_set:
                # Find next-best among kept.
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
        # Rebuild docs_per_chapter for kept only.
        docs_per_chapter = {ci: [] for ci in kept}
        for k, ci in doc_to_chapter.items():
            docs_per_chapter[ci].append(k)

    # Final chapter list (reduce_node-compatible schema).
    out_chapters: list[dict] = []
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

    # Persist chapter_select-specific blob.
    select_payload = {
        "prompt_version":     _PROMPT_VERSION,
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
    blob = json.dumps(select_payload, indent=2, ensure_ascii=False)
    await minio.write(vkey_select, blob, content_type="application/json")
    await minio.write(lkey_select, blob, content_type="application/json")

    # ALSO persist in legacy reduce_node schema so order_chapters +
    # plan_write read it transparently.
    plan_payload = {
        "prompt_version":  _PROMPT_VERSION,
        "framework_slug":  slug,
        "manifest_hash":   manifest,
        "outline": {
            "chapters": [
                {
                    "title":              c["title"],
                    "description":        c["description"],
                    "member_cluster_ids": [],   # not applicable — LLM-first path
                    "member_doc_keys":    c["member_doc_keys"],
                    "order":              c["order"],
                }
                for c in out_chapters
            ],
        },
        "n_clusters_in":   len(proposals),    # mirror reduce schema
        "n_chapters_out":  len(kept),
        "n_repairs":       0,
        "forced_repair":   False,
        "source":          "llm_first_chapter_select_v1",
    }
    plan_vkey = _chapter_plan_versioned_key(slug, manifest)
    plan_lkey = _chapter_plan_latest_key(slug)
    plan_blob = json.dumps(plan_payload, indent=2, ensure_ascii=False)
    await minio.write(plan_vkey, plan_blob, content_type="application/json")
    await minio.write(plan_lkey, plan_blob, content_type="application/json")

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
        n_chapters=len(kept), n_pruned=len(pruned),
        coverage=stats["coverage_fraction"], wall_ms=wall_ms,
        titles=stats["chapter_titles"],
    )
    return {"chapter_plan_ref": plan_lkey, "select_stats": stats}
