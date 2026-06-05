"""chapter_select — pure algorithm (greedy coverage + manifest hash)."""
from __future__ import annotations

from hashlib import sha256

from .params import CONFIDENCE_THRESHOLD, COVERAGE_TARGET
from .versions import PROMPT_VERSION


def greedy_select(
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
        if any(c >= CONFIDENCE_THRESHOLD for c in cv.values())
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
                if doc_confidences[k].get(ci, 0.0) >= CONFIDENCE_THRESHOLD:
                    covered.add(k)

    # Greedy: pick chapter maximizing sum of confidences over uncovered docs.
    coverage = len(covered) / n_assignable if n_assignable else 1.0
    while coverage < COVERAGE_TARGET and len(selected) < n_proposals:
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
                if c >= CONFIDENCE_THRESHOLD:
                    gain += c
            if gain > best_gain:
                best_gain = gain
                best_idx = ci
        if best_idx < 0 or best_gain <= 0.0:
            break
        selected.append(best_idx)
        selected_set.add(best_idx)
        for k in assignable:
            if doc_confidences[k].get(best_idx, 0.0) >= CONFIDENCE_THRESHOLD:
                covered.add(k)
        coverage = len(covered) / n_assignable if n_assignable else 1.0

    # Assign each doc to its highest-confidence SELECTED chapter; sub-threshold
    # docs still land somewhere to preserve lineage.
    doc_to_chapter: dict[str, int] = {}
    for k in assignments.keys():
        cv = doc_confidences.get(k) or {}
        sel_scores = [(ci, cv.get(ci, 0.0)) for ci in selected_set]
        if not sel_scores:
            continue
        sel_scores.sort(key = lambda p: p[1], reverse = True)
        best_ci, best_c = sel_scores[0]
        if best_c <= 0.0 and k not in assignable:
            # Doc had no signal anywhere; skip assignment.
            continue
        doc_to_chapter[k] = best_ci

    return selected, doc_to_chapter


def detect_pinned_indices(proposals: list[dict], seeds: dict) -> set[int]:
    """Reserved for namespace-based pinning. Empty by default — pinning
    here risks locking weak proposals; seeds only influence propose-time."""
    return set()


def manifest_hash(
    *, slug: str, proposals_ref: str, assignments_ref: str,
) -> str:
    h = sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(slug.encode())
    h.update(b"|")
    h.update(proposals_ref.encode())
    h.update(b"|")
    h.update(assignments_ref.encode())
    return h.hexdigest()[:16]
