"""chapter_select — pure algorithm (greedy coverage + manifest hash)."""
from __future__ import annotations
from . import params, versions

from hashlib import sha256



def greedy_select(
    *,
    proposals: list[dict],
    assignments: dict[str, list[dict]],
    pinned_indices: set[int],
) -> tuple[list[int], dict[str, int]]:
    """Greedy coverage: returns (selected_indices, doc_to_chapter) where doc_to_chapter maps to highest-confidence selected chapter."""
    n_proposals = len(proposals)
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
        if any(c >= params.CONFIDENCE_THRESHOLD for c in cv.values())
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
                if doc_confidences[k].get(ci, 0.0) >= params.CONFIDENCE_THRESHOLD:
                    covered.add(k)

    # Greedy: pick chapter maximizing sum of confidences over uncovered docs.
    coverage = len(covered) / n_assignable if n_assignable else 1.0
    while coverage < params.COVERAGE_TARGET and len(selected) < n_proposals:
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
                if c >= params.CONFIDENCE_THRESHOLD:
                    gain += c
            if gain > best_gain:
                best_gain = gain
                best_idx = ci
        if best_idx < 0 or best_gain <= 0.0:
            break
        selected.append(best_idx)
        selected_set.add(best_idx)
        for k in assignable:
            if doc_confidences[k].get(best_idx, 0.0) >= params.CONFIDENCE_THRESHOLD:
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


def prune_and_finalize_selection(
    *,
    selected: list[int],
    doc_to_chapter: dict[str, int],
    assignments: dict[str, list[dict]],
    pinned: set[int],
    proposals: list[dict],
) -> dict:
    """Orphan-protected pruning of small/unpinned chapters (a member doc is
    at risk if removing its only <MIN_DOCS_PER_CHAPTER chapter leaves it
    with no other above-threshold chapter) + doc reassignment from pruned →
    next-best kept chapter + the reduce_node-compatible chapter list.
    Returns {kept, pruned, orphan_protected, doc_to_chapter, out_chapters}."""
    docs_per_chapter: dict[int, list[str]] = {ci: [] for ci in selected}
    for k, ci in doc_to_chapter.items():
        if ci in docs_per_chapter:
            docs_per_chapter[ci].append(k)

    above_threshold_chapters: dict[str, set[int]] = {}
    for k, scores in assignments.items():
        above_threshold_chapters[k] = {
            int(s["chapter_idx"])
            for s in scores
            if float(s.get("confidence") or 0.0) >= params.CONFIDENCE_THRESHOLD
            and int(s["chapter_idx"]) in set(selected)
        }

    pruned: list[int] = []
    kept: list[int] = []
    orphan_protected: list[int] = []
    for ci in selected:
        members = docs_per_chapter.get(ci, [])
        below_min = len(members) < params.MIN_DOCS_PER_CHAPTER
        if not below_min:
            kept.append(ci)
            continue
        if ci in pinned:
            kept.append(ci)
            continue
        # Below-min, unpinned: check for orphan risk. A member doc is at
        # risk if removing `ci` leaves it with no above-threshold chapter.
        creates_orphan = False
        for k in members:
            alternatives = above_threshold_chapters.get(k, set()) - {ci}
            if not alternatives:
                creates_orphan = True
                break
        if creates_orphan:
            kept.append(ci)
            orphan_protected.append(ci)
        else:
            pruned.append(ci)

    # Restore lowest-pruned (by doc count) if too few chapters kept.
    if len(kept) < params.MIN_KEPT_CHAPTERS and pruned:
        pruned_sorted = sorted(
            pruned,
            key = lambda ci: len(docs_per_chapter.get(ci, [])),
            reverse = True,
        )
        while len(kept) < params.MIN_KEPT_CHAPTERS and pruned_sorted:
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

    return {
        "kept":             kept,
        "pruned":           pruned,
        "orphan_protected": orphan_protected,
        "doc_to_chapter":   doc_to_chapter,
        "out_chapters":     out_chapters,
    }


def manifest_hash(
    *, slug: str, proposals_ref: str, assignments_ref: str,
) -> str:
    h = sha256()
    h.update(versions.PROMPT_VERSION.encode())
    h.update(slug.encode())
    h.update(b"|")
    h.update(proposals_ref.encode())
    h.update(b"|")
    h.update(assignments_ref.encode())
    return h.hexdigest()[:16]
