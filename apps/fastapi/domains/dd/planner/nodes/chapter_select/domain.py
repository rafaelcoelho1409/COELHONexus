"""chapter_select — pure algorithm (greedy coverage + manifest hash)."""
from __future__ import annotations
from . import params, versions

import math
import re
from collections import Counter, defaultdict
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

    # Floor: coverage-driven stopping can finish with 1-2 chapters when one
    # broad chapter ("API Reference") absorbs >=95% of docs. Top up to
    # MIN_KEPT_CHAPTERS with the next-best chapters by total confidence mass.
    if n_proposals >= params.MIN_KEPT_CHAPTERS:
        mass = {
            ci: sum(cv.get(ci, 0.0) for cv in doc_confidences.values())
            for ci in range(n_proposals) if ci not in selected_set
        }
        for ci, _m in sorted(mass.items(), key = lambda p: p[1], reverse = True):
            if len(selected) >= params.MIN_KEPT_CHAPTERS:
                break
            selected.append(ci)
            selected_set.add(ci)

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


_STOP_TOKENS = frozenset({
    "api", "apis", "the", "of", "and", "for", "an", "to", "in", "on", "with",
    "client", "python", "documentation", "docs", "guide", "reference",
})


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(t) > 1 and t not in _STOP_TOKENS
    }


def _slot_tokens(slot: dict) -> set[str]:
    return _tokens(" ".join([
        slot.get("title") or "", slot.get("description") or "",
        " ".join(slot.get("key_concepts") or []),
    ]))


def consolidate_placements(
    *,
    kept: list[int],
    doc_to_chapter: dict[str, int],
    assignments: dict[str, list[dict]],
    proposals: list[dict],
    doc_family: dict[str, str],
    doc_title: dict[str, str],
    anchored_family: dict[str, str] | None = None,
) -> dict:
    """Make the plan place EVERY doc, and place it sensibly.

    `greedy_select` + `prune_and_finalize_selection` left three gaps on
    API-reference corpora (elasticsearch-python: 53 of 694 docs silently
    absent from the plan, `n_dropped: 0`; 26 more stuffed into the catch-all
    'Elasticsearch API'; the ML namespace split across two chapters):
      * docs scored 0.0 against every proposal (a namespace nobody proposed a
        chapter for) were skipped as "no signal";
      * docs whose best proposal was never selected landed in an arbitrary
        selected chapter at confidence 0;
      * per-doc assignment is family-blind, so one reference page's docs can
        be torn between two chapters.
    Steps, in order: (0) re-home a *torn* family into the chapter its page
    title matches (only docs the assigner scored ≥ threshold there); (1) an
    unselected proposal that still owns ≥ MIN_DOCS weak/unplaced docs is selected
    after all, taking along the docs that strictly prefer it; (2) remaining
    weak/unplaced docs follow their family's chapter; (3) remaining families of
    ≥ MIN_DOCS docs become a gap chapter named after their page; (4) leftovers go
    to the lexically nearest chapter. Last, (5) harmonizes ANCHORED
    families of any size ≥ FAMILY_HARMONIZE_MIN_DOCS: docs outside the family's
    majority chapter follow it when the assigner rated that chapter a plausible
    home (`anchored_family` — only virtual sub-pages of one page; see params).
    Pure + deterministic. `doc_family` / `doc_title` may be partial.
    Returns {kept, doc_to_chapter, out_chapters, stats}; slot ids are
    proposal indices, or negative ints for gap chapters."""
    thr = params.CONFIDENCE_THRESHOLD
    min_docs = params.MIN_DOCS_PER_CHAPTER

    scores: dict[str, dict[int, float]] = {
        k: {int(s["chapter_idx"]): float(s.get("confidence") or 0.0) for s in sc}
        for k, sc in assignments.items()
    }

    def conf(k: str, slot: int) -> float:
        return scores.get(k, {}).get(slot, 0.0) if slot >= 0 else 0.0

    def best_proposal(k: str) -> tuple[int, float]:
        sc = scores.get(k) or {}
        if not sc:
            return -1, 0.0
        ci = min(sc, key = lambda c: (-sc[c], c))
        return ci, sc[ci]

    slots: dict[int, dict] = {}

    def _open_slot(slot: int, p: dict, origin: str) -> None:
        slots[slot] = {
            "title":       p.get("title"),
            "description": p.get("description"),
            "key_concepts": p.get("key_concepts") or [],
            "origin":      origin,
            "proposal_idx": slot if slot >= 0 else None,
        }

    for ci in kept:
        _open_slot(ci, proposals[ci], "proposal")
    place: dict[str, int] = {k: ci for k, ci in doc_to_chapter.items() if ci in slots}

    stats = {
        "n_family_consolidated": 0, "n_family_harmonized": 0, "n_rehomed_to_family": 0,
        "n_rescued_chapters": 0, "n_rescue_pulled": 0, "n_gap_chapters": 0,
        "n_leftover_placed": 0, "n_unplaced_final": 0,
    }

    families: dict[str, list[str]] = defaultdict(list)
    for k in sorted(assignments):
        families[doc_family.get(k) or k].append(k)

    def family_title(f: str) -> str:
        c = Counter(
            (doc_title.get(k) or "").split(" — ")[0].strip()
            for k in families.get(f, []) if doc_title.get(k)
        )
        c.pop("", None)
        if c:
            return c.most_common(1)[0][0]
        base = f.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return re.sub(r"[-_]+", " ", base).strip().title() or "Additional Topics"

    # (0) torn families → the chapter whose title matches the page's title.
    for f in sorted(families):
        docs = families[f]
        if len(docs) < params.TORN_FAMILY_MIN_DOCS:
            continue
        counts = Counter(place[k] for k in docs if k in place)
        if len(counts) < 2:
            continue
        (a, na), (b, nb) = sorted(counts.items(), key = lambda kv: (-kv[1], kv[0]))[:2]
        n = len(docs)
        if (na + nb) / n < params.TORN_FAMILY_TOP2_SHARE or nb / n < params.TORN_FAMILY_MIN_MINOR:
            continue
        ft = _tokens(family_title(f))
        sa, sb = len(ft & _slot_tokens(slots[a])), len(ft & _slot_tokens(slots[b]))
        if sa == sb:
            continue
        win, lose = (a, b) if sa > sb else (b, a)
        for k in docs:
            # A title match alone must never override the assigner: only move a
            # doc the assigner itself scored as a valid home for the winning
            # chapter. (asyncio: 7 pages scored 0.7-1.0 for "Task Management" and
            # 0.0-0.3 for "…Coroutines" were moved on the word "coroutines" in
            # their page title, gutting the task chapter.)
            if place.get(k) == lose and conf(k, win) >= thr:
                place[k] = win
                stats["n_family_consolidated"] += 1

    # candidates: unplaced docs + docs sitting in a chapter that scored them < threshold
    cands = {k for k in assignments if k not in place}
    cands |= {k for k, c in place.items() if conf(k, c) < thr}

    # (1) rescue unselected proposals — before the family-follow step, which would
    # otherwise pull their weak docs into whichever chapter the rest of the page
    # landed in (asyncio: the call-graph pages were dragged into "Futures" although
    # they scored 1.0 for their own proposal).
    #   weak:   weak/unplaced docs whose best proposal is p (conf ≥ thr) — the trigger
    #   prefer: confidently placed docs that strictly prefer p (conf ≥ PREFER_MIN_CONF,
    #           above their current chapter's score) — they move along once triggered
    unsel: dict[int, dict[str, list[str]]] = defaultdict(lambda: {"weak": [], "prefer": []})
    for k in sorted(assignments):
        ci, c = best_proposal(k)
        if ci < 0 or ci in slots or c < thr:
            continue
        if k in cands:
            unsel[ci]["weak"].append(k)
        elif c >= params.PREFER_MIN_CONF and c > conf(k, place[k]):
            unsel[ci]["prefer"].append(k)
    for ci in sorted(unsel):
        weak, prefer = unsel[ci]["weak"], unsel[ci]["prefer"]
        if len(weak) >= min_docs:
            _open_slot(ci, proposals[ci], "rescued")
            for k in weak + prefer:
                place[k] = ci
                cands.discard(k)
            stats["n_rescued_chapters"] += 1
            stats["n_rescue_pulled"] += len(prefer)

    # (2) remaining weak/unplaced docs follow the family's (confidently placed) chapter.
    for k in sorted(cands):
        fam_docs = families.get(doc_family.get(k) or k, [])
        strong = [place[j] for j in fam_docs if j in place and j not in cands]
        # A handful of confident siblings must not drag a whole namespace
        # along (3 stray Watcher pages would otherwise pull all 13 into the
        # catch-all chapter): the family has to be mostly confidently placed.
        if not strong or len(strong) / len(fam_docs) < params.FAMILY_MIN_STRONG_SHARE:
            continue
        dom, n = Counter(strong).most_common(1)[0]
        if n / len(strong) >= params.FAMILY_DOMINANCE:
            if place.get(k) != dom:
                stats["n_rehomed_to_family"] += 1
            place[k] = dom
            cands.discard(k)

    # (3) gap chapters for families nobody proposed a chapter for.
    gap_fams: dict[str, list[str]] = defaultdict(list)
    for k in sorted(cands):
        if k not in place:                       # weak docs keep their placement
            gap_fams[doc_family.get(k) or k].append(k)
    next_gap = -1
    for f in sorted(gap_fams):
        docs = gap_fams[f]
        if len(docs) < min_docs:
            continue
        title = family_title(f)
        _open_slot(next_gap, {
            "title": title,
            "description": f"Reference documentation for {title} ({len(docs)} pages).",
            "key_concepts": [],
        }, "gap")
        for k in docs:
            place[k] = next_gap
            cands.discard(k)
        next_gap -= 1
        stats["n_gap_chapters"] += 1

    # (4) leftovers → lexically nearest chapter; no overlap anywhere → the largest (catch-all) chapter.
    sizes = Counter(place.values())
    slot_toks = {sid: _slot_tokens(sl) for sid, sl in slots.items()}
    for k in sorted(k for k in cands if k not in place):
        dt = _tokens(f"{doc_title.get(k) or ''} {doc_family.get(k) or ''}")
        best = max(
            slots,
            key = lambda sid: (len(dt & slot_toks[sid]), sizes.get(sid, 0), -sid),
        )
        place[k] = best
        sizes[best] += 1
        stats["n_leftover_placed"] += 1

    # (5) harmonize anchored families — LAST, once every chapter that will exist is
    # open (rescue/gap steps above can open a family's real home): sub-pages of one
    # reference page belong together. A minority doc follows the family's majority chapter when that
    # chapter is a plausible home by the assigner's own score and its current
    # chapter doesn't beat it by much — a strong, specific disagreement stays.
    # (asyncio: subprocess 6/1, queue 3/1, exceptions 2/1 split across chapters.)
    if anchored_family:
        fam_docs: dict[str, list[str]] = defaultdict(list)
        for k in sorted(assignments):
            f = anchored_family.get(k)
            if f:
                fam_docs[f].append(k)
        max_family = max(params.FAMILY_HARMONIZE_MIN_DOCS,
                         int(params.FAMILY_HARMONIZE_MAX_SHARE * len(assignments)))
        for f in sorted(fam_docs):
            docs = fam_docs[f]
            if not (params.FAMILY_HARMONIZE_MIN_DOCS <= len(docs) <= max_family):
                continue
            placed = [k for k in docs if k in place]
            if len(placed) < params.FAMILY_HARMONIZE_MIN_DOCS:
                continue
            counts = Counter(place[k] for k in placed)
            (maj, n_maj), = counts.most_common(1)
            if len(counts) < 2 or n_maj / len(placed) < params.FAMILY_HARMONIZE_MAJORITY:
                continue
            for k in placed:
                cur = place[k]
                if cur == maj:
                    continue
                c_maj, c_cur = conf(k, maj), conf(k, cur)
                if c_maj >= params.HARMONIZE_MIN_CONF and (c_cur - c_maj) <= params.HARMONIZE_MAX_GAP:
                    place[k] = maj
                    stats["n_family_harmonized"] += 1

    stats["n_unplaced_final"] = sum(1 for k in assignments if k not in place)

    members: dict[int, list[str]] = defaultdict(list)
    for k, sid in place.items():
        members[sid].append(k)
    order = [sid for sid in slots if members.get(sid)]
    out_chapters = []
    for i, sid in enumerate(order, 1):
        sl = slots[sid]
        out_chapters.append({
            "title":               sl["title"],
            "description":         sl["description"],
            "key_concepts":        sl["key_concepts"],
            "member_doc_keys":     sorted(members[sid]),
            "n_member_docs":       len(members[sid]),
            "order":               i,
            "source_proposal_idx": sl["proposal_idx"],
            "origin":              sl["origin"],
            "pinned":              False,
        })
    return {
        "kept":           [sid for sid in order if sid >= 0],
        "doc_to_chapter": place,
        "out_chapters":   out_chapters,
        "stats":          stats,
    }


def anchored_family_map(entries: list[dict]) -> dict[str, str]:
    """key → parent-page URL for every entry under a URL that was split into
    virtual sub-pages (at least one entry carries anchors) — the page's own intro
    doc (no anchors itself) is a member too. Only these form families for
    harmonization/splitting: on a flat site, or a single-URL dump, the URL says
    nothing about kinship."""
    parent = lambda e: (e.get("url") or "").split("#", 1)[0]
    split_pages = {parent(e) for e in entries if e.get("anchors") and parent(e)}
    return {e["key"]: parent(e) for e in entries if e.get("key") and parent(e) in split_pages}


def _renumber(chapters: list[dict]) -> list[dict]:
    for i, c in enumerate(chapters, 1):
        c["order"] = i
        c["n_member_docs"] = len(c["member_doc_keys"])
    return chapters


def apply_foundations(
    *,
    out_chapters: list[dict],
    doc_role: dict[str, str],
    doc_family: dict[str, str],
    key_idx: dict[str, int],
) -> tuple[list[dict], dict]:
    """Collect cross-cutting single pages into one front-matter chapter.

    A doc qualifies when doc_distill labelled its role in FOUNDATIONS_ROLES AND it
    stands alone on its page (no siblings under the same parent URL) — reference
    sub-pages come in families, overview/platform/debugging pages do not. At most
    FOUNDATIONS_MAX_SHARE of the corpus is moved (overview first, then source
    order), never from a chapter that would drop below MIN_DOCS_PER_CHAPTER, and
    nothing happens below FOUNDATIONS_MIN_DOCS. Returns (chapters, stats)."""
    total = sum(len(c["member_doc_keys"]) for c in out_chapters)
    fam_size = Counter(doc_family.get(k) or k for c in out_chapters for k in c["member_doc_keys"])
    cands = [
        (c_i, k)
        for c_i, c in enumerate(out_chapters)
        for k in c["member_doc_keys"]
        if doc_role.get(k) in params.FOUNDATIONS_ROLES and fam_size[doc_family.get(k) or k] == 1
    ]
    stats = {"n_foundations": 0, "foundations_candidates": len(cands)}
    if len(cands) < params.FOUNDATIONS_MIN_DOCS:
        return out_chapters, stats
    cap = max(params.FOUNDATIONS_MIN_DOCS, int(params.FOUNDATIONS_MAX_SHARE * total))
    cands.sort(key = lambda ck: (
        0 if doc_role.get(ck[1]) == "overview" else 1, key_idx.get(ck[1], 1 << 30), ck[1],
    ))
    remaining = {i: len(c["member_doc_keys"]) for i, c in enumerate(out_chapters)}
    moved: list[tuple[int, str]] = []
    for c_i, k in cands:
        if len(moved) >= cap:
            break
        if remaining[c_i] - 1 < params.MIN_DOCS_PER_CHAPTER:
            continue
        remaining[c_i] -= 1
        moved.append((c_i, k))
    if len(moved) < params.FOUNDATIONS_MIN_DOCS:
        return out_chapters, stats
    moved_keys = {k for _, k in moved}
    new_chapters = []
    for c in out_chapters:
        members = [k for k in c["member_doc_keys"] if k not in moved_keys]
        new_chapters.append({**c, "member_doc_keys": members})
    foundations = {
        "title":               params.FOUNDATIONS_TITLE,
        "description":         params.FOUNDATIONS_DESCRIPTION,
        "key_concepts":        [],
        "member_doc_keys":     sorted(moved_keys, key = lambda k: (key_idx.get(k, 1 << 30), k)),
        "n_member_docs":       len(moved_keys),
        "order":               1,
        "source_proposal_idx": None,
        "origin":              "foundations",
        "role":                "foundations",
        "pinned":              False,
    }
    stats["n_foundations"] = len(moved_keys)
    return _renumber([foundations] + new_chapters), stats


def plan_splits(out_chapters: list[dict], max_chapters: int = 30) -> list[tuple[int, int]]:
    """[(chapter position, n_parts)] for chapters over SPLIT_MAX_SHARE of the corpus."""
    total = sum(len(c["member_doc_keys"]) for c in out_chapters)
    if total < params.SPLIT_MIN_TOTAL_DOCS:
        return []
    out: list[tuple[int, int]] = []
    n_chapters = len(out_chapters)
    sizes = sorted(len(c["member_doc_keys"]) for c in out_chapters if c.get("origin") != "foundations")
    median = sizes[len(sizes) // 2] if sizes else 0
    for i, c in enumerate(out_chapters):
        n = len(c["member_doc_keys"])
        if c.get("origin") == "foundations" or n < params.SPLIT_MIN_CHAPTER_DOCS:
            continue
        share = n / total
        if share <= params.SPLIT_MAX_SHARE or n <= params.SPLIT_MEDIAN_RATIO * median:
            continue
        k = min(
            params.SPLIT_MAX_PARTS,
            max(2, math.ceil(share / params.SPLIT_MAX_SHARE)),
            n // params.MIN_DOCS_PER_CHAPTER,
        )
        if k >= 2 and n_chapters + (k - 1) <= max_chapters:
            out.append((i, k))
            n_chapters += k - 1
    return out


def _unit_label(title: str) -> str:
    return (title or "").split(" — ")[0].strip() or "(untitled)"


def build_split_units(
    member_keys: list[str],
    *,
    anchored_family: dict[str, str],
    doc_title: dict[str, str],
    distillates: dict[str, dict],
    key_idx: dict[str, int],
) -> list[dict]:
    """The items an oversize chapter is partitioned over: anchored sibling
    sub-pages of one page are ONE unit (so a split can never tear a family);
    every other doc is its own unit. Ordered by position in the source docs."""
    groups: dict[str, list[str]] = defaultdict(list)
    for k in member_keys:
        groups[anchored_family.get(k) or k].append(k)
    units = []
    for g, keys in groups.items():
        keys = sorted(keys, key = lambda k: (key_idx.get(k, 1 << 30), k))
        summ = " ".join(
            ((distillates.get(k) or {}).get("summary") or "").strip() for k in keys[:3]
        ).strip()
        units.append({
            "keys":    keys,
            "label":   _unit_label(doc_title.get(keys[0], "")),
            "summary": summ[:params.SPLIT_UNIT_SUMMARY_CHARS],
        })
    units.sort(key = lambda u: (key_idx.get(u["keys"][0], 1 << 30), u["keys"][0]))
    for i, u in enumerate(units):
        u["id"] = i
    return units


def validate_split(
    parts: list[dict], units: list[dict], other_titles: set[str],
) -> tuple[bool, str]:
    """Check an LLM split proposal: parts = [{title, description, unit_ids}].
    Pure. Returns (ok, reason) — the reason is fed back to the model on repair."""
    n_docs = sum(len(u["keys"]) for u in units)
    if not (2 <= len(parts) <= params.SPLIT_MAX_PARTS):
        return False, f"need between 2 and {params.SPLIT_MAX_PARTS} groups, got {len(parts)}"
    seen: set[int] = set()
    for p in parts:
        for u in p.get("unit_ids") or []:
            if u in seen:
                return False, f"unit {u} appears in more than one group"
            seen.add(u)
    all_ids = {u["id"] for u in units}
    if seen - all_ids:
        return False, f"unknown unit ids {sorted(seen - all_ids)[:10]}"
    if all_ids - seen:
        return False, f"units not placed in any group: {sorted(all_ids - seen)[:10]}"
    floor = max(params.MIN_DOCS_PER_CHAPTER, math.ceil(0.15 * n_docs))
    titles: set[str] = set()
    for p in parts:
        size = sum(len(units[u]["keys"]) for u in p["unit_ids"])
        if size < floor:
            return False, f"group {p.get('title')!r} has only {size} docs (minimum {floor})"
        t = " ".join((p.get("title") or "").split())
        if not (2 <= len(t.split()) <= 8):
            return False, f"title {t!r} must be 2-8 words"
        if t.casefold() in titles or t.casefold() in other_titles:
            return False, f"title {t!r} is not unique among chapters"
        titles.add(t.casefold())
    return True, ""


def apply_split(chapter: dict, parts: list[dict], units: list[dict]) -> list[dict]:
    """Replace one chapter by its validated parts (order within the chapter by source position)."""
    out = []
    for j, p in enumerate(parts):
        keys = sorted(k for u in p["unit_ids"] for k in units[u]["keys"])
        out.append({
            **chapter,
            "title":               " ".join(p["title"].split()),
            "description":         (p.get("description") or chapter.get("description") or "").strip(),
            "key_concepts":        [],
            "member_doc_keys":     keys,
            "n_member_docs":       len(keys),
            "source_proposal_idx": chapter.get("source_proposal_idx") if j == 0 else None,
            "origin":              "split",
        })
    return out


def select_pipeline(
    *,
    proposals: list[dict],
    assignments: dict[str, list[dict]],
    seeds: dict,
    doc_family: dict[str, str],
    doc_title: dict[str, str],
    distillates: dict[str, dict],
    key_idx: dict[str, int],
    anchored_family: dict[str, str] | None = None,
) -> dict:
    """The whole deterministic selection: greedy coverage → prune → consolidate
    (incl. anchored-family harmonization) → Foundations chapter. Shared by the
    node and the offline replay (scripts/dd/replay_select.py), so what is
    measured is what ships. The LLM oversize-split runs after this, in the node."""
    pinned = detect_pinned_indices(proposals, seeds)
    selected, doc_to_chapter = greedy_select(
        proposals = proposals, assignments = assignments, pinned_indices = pinned,
    )
    final = prune_and_finalize_selection(
        selected = selected, doc_to_chapter = doc_to_chapter, assignments = assignments,
        pinned = pinned, proposals = proposals,
    )
    cons = consolidate_placements(
        kept = final["kept"], doc_to_chapter = final["doc_to_chapter"],
        assignments = assignments, proposals = proposals,
        doc_family = doc_family, doc_title = doc_title, anchored_family = anchored_family,
    )
    doc_role = {k: (d or {}).get("role") for k, d in (distillates or {}).items()}
    chapters, f_stats = apply_foundations(
        out_chapters = cons["out_chapters"], doc_role = doc_role,
        doc_family = doc_family, key_idx = key_idx,
    )
    stats = {**cons["stats"], **f_stats}
    return {
        "kept":             cons["kept"],
        "pruned":           final["pruned"],
        "orphan_protected": final["orphan_protected"],
        "pinned":           pinned,
        "out_chapters":     chapters,
        "doc_to_chapter":   {k: i for i, c in enumerate(chapters) for k in c["member_doc_keys"]},
        "stats":            stats,
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
