"""chapter_select — pure algorithm (greedy coverage + manifest hash)."""
from __future__ import annotations
import domains
from . import params, patterns, versions

import math
import os
import re
from collections import Counter, defaultdict
from hashlib import sha256



# ---------------------------------------------------------------------------
# Word-overlap evidence (IDF-weighted cosine) — breaks assigner ties and drives the
# cluster-based split. Same word model as chapter_propose's candidate scoring.
# ---------------------------------------------------------------------------

def _word_idf(word_sets: list[set[str]]) -> tuple[dict[str, float], float]:
    n = max(1, len(word_sets))
    df = Counter(w for ws in word_sets for w in ws)
    return {w: math.log(1.0 + n / c) for w, c in df.items()}, math.log(1.0 + n)


def _word_vec(ws: set[str], idf: dict[str, float], unseen: float) -> dict[str, float]:
    v = {w: idf.get(w, unseen) for w in ws}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {w: x / norm for w, x in v.items()}


def _cosine(u: dict[str, float], v: dict[str, float]) -> float:
    if len(u) > len(v):
        u, v = v, u
    return sum(x * v[w] for w, x in u.items() if w in v)


def doc_match_texts(keys: list[str], distillates: dict[str, dict]) -> dict[str, str]:
    """key → the text a page is matched on (slug + distillate summary and terms)."""
    dom = domains.dd.planner.nodes.chapter_propose.domain
    return {k: dom.doc_match_text(k, (distillates or {}).get(k)) for k in keys}


def doc_affinity(
    proposals: list[dict], doc_texts: dict[str, str],
) -> dict[str, list[float]]:
    """key → cosine of the page with every proposal (title + description + key concepts)."""
    if not doc_texts or not proposals:
        return {}
    words = domains.dd.planner.nodes.chapter_propose.domain.match_words
    doc_ws = {k: words(t) for k, t in doc_texts.items()}
    idf, unseen = _word_idf(list(doc_ws.values()))
    chap = [
        _word_vec(words(" ".join([
            p.get("title") or "", p.get("description") or "", " ".join(p.get("key_concepts") or []),
        ])), idf, unseen)
        for p in proposals
    ]
    return {
        k: [round(_cosine(_word_vec(ws, idf, unseen), cv), 4) for cv in chap]
        for k, ws in doc_ws.items()
    }


def _tie_key(conf: float, aff: float, slot: int) -> tuple:
    """Sort key picking the best chapter: confidence (ties to TIE_DECIMALS), then word
    overlap, then lower index."""
    return (-round(conf, params.TIE_DECIMALS), -aff, slot)


def greedy_select(
    *,
    proposals: list[dict],
    assignments: dict[str, list[dict]],
    pinned_indices: set[int],
    affinity: dict[str, list[float]] | None = None,
) -> tuple[list[int], dict[str, int]]:
    """Greedy coverage: returns (selected_indices, doc_to_chapter) where doc_to_chapter maps to
    the highest-confidence selected chapter (equal confidences: the larger word overlap)."""
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
        aff = (affinity or {}).get(k) or []
        sel_scores.sort(key = lambda p: _tie_key(p[1], aff[p[0]] if p[0] < len(aff) else 0.0, p[0]))
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
    affinity: dict[str, list[float]] | None = None,
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
                best_key = None
                aff = (affinity or {}).get(k) or []
                for s in scores:
                    si = s.get("chapter_idx")
                    sc = float(s.get("confidence") or 0.0)
                    if si in kept_set:
                        key = _tie_key(sc, aff[si] if si < len(aff) else 0.0, si)
                        if best_key is None or key < best_key:
                            best_key = key
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


def _page_title(title: str | None) -> str:
    return (title or "").split(" — ")[0].strip()


def _is_page_family(f: str, docs: list[str], doc_title: dict[str, str]) -> bool:
    """True when the family's docs are sub-pages of ONE page (their titles share a
    page title). False for a single-URL dump, where every doc has its own title."""
    titles = [_page_title(doc_title.get(k)) for k in docs if doc_title.get(k)]
    if not titles:
        return True          # nothing to judge by: keep the URL-derived name
    top = Counter(titles).most_common(1)[0][1]
    return top / len(docs) >= params.GAP_FAMILY_TITLE_SHARE


def _appendix_description(docs: list[str], doc_title: dict[str, str]) -> str:
    """"Pages that fit no other chapter (N): a, b, c, …" — quoting titles with the
    prefix common to the whole dump (the site slug) stripped."""
    titles = [_page_title(doc_title.get(k)) or k for k in sorted(docs)]
    pre = os.path.commonprefix(titles)
    cut = pre.rfind("-") + 1 if "-" in pre else 0
    shown = [re.sub(r"[-_]+", " ", t[cut:]).strip() or t for t in titles][:params.GAP_APPENDIX_LISTED]
    more = f", +{len(titles) - len(shown)} more" if len(titles) > len(shown) else ""
    return f"Pages that fit no other chapter ({len(titles)}): {', '.join(shown)}{more}."


def consolidate_placements(
    *,
    kept: list[int],
    doc_to_chapter: dict[str, int],
    assignments: dict[str, list[dict]],
    proposals: list[dict],
    doc_family: dict[str, str],
    doc_title: dict[str, str],
    anchored_family: dict[str, str] | None = None,
    affinity: dict[str, list[float]] | None = None,
    key_dir: dict[str, tuple] | None = None,
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
        aff = (affinity or {}).get(k) or []
        ci = min(sc, key = lambda c: _tie_key(sc[c], aff[c] if 0 <= c < len(aff) else 0.0, c))
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
        "n_leftover_placed": 0, "n_leftover_appendix": 0, "n_unplaced_final": 0,
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
        if _is_page_family(f, docs, doc_title):
            title = family_title(f)
            _open_slot(next_gap, {
                "title": title,
                "description": f"Reference documentation for {title} ({len(docs)} pages).",
                "key_concepts": [],
            }, "gap")
        else:
            _open_slot(next_gap, {
                "title": params.GAP_APPENDIX_TITLE,
                "description": _appendix_description(docs, doc_title),
                "key_concepts": [],
            }, "gap")
            slots[next_gap]["role"] = "appendix"
        for k in docs:
            place[k] = next_gap
            cands.discard(k)
        next_gap -= 1
        stats["n_gap_chapters"] += 1

    # (3a) a doc no proposal fits, whose URL directory is mostly confidently placed in ONE chapter,
    # joins that chapter (langfuse: self-hosting/configuration/observability went to "Framework and
    # Model Provider Integrations" on the title word "observability"; its 8 siblings were in "Administration").
    hold: dict[tuple, Counter] = defaultdict(Counter)
    if key_dir:
        for k, sid in place.items():
            if k in cands or slots[sid].get("role") == "appendix":
                continue
            d = key_dir.get(k) or ()
            for depth in range(params.DIR_OWNER_MIN_DEPTH, len(d) + 1):
                hold[d[:depth]][sid] += 1

    def dir_owner(k: str) -> int | None:
        d = key_dir.get(k) or () if key_dir else ()
        for depth in range(len(d), params.DIR_OWNER_MIN_DEPTH - 1, -1):
            c = hold.get(d[:depth])
            if not c:
                continue
            n = sum(c.values())
            sid, top = c.most_common(1)[0]
            if n >= params.DIR_OWNER_MIN_DOCS and top / n >= params.DIR_OWNER_MIN_SHARE:
                return sid
        return None

    stats["n_leftover_dir_owned"] = 0
    nofit = sorted(k for k in cands if k in assignments and best_proposal(k)[1] < thr)
    big_pool = (len(nofit) >= params.LEFTOVER_APPENDIX_MIN_DOCS
                and len(nofit) >= params.LEFTOVER_APPENDIX_MIN_SHARE * len(assignments))
    for k in ([] if big_pool else nofit):        # a big pool is a topic of its own (3b), not a set of strays
        sid = dir_owner(k)
        if sid is not None:
            place[k] = sid
            cands.discard(k)
            stats["n_leftover_dir_owned"] += 1

    # (3b) a big pool of docs NO proposal fits (best score under the threshold everywhere) is not
    # a handful of strays: lexical placement scatters it into whichever chapter shares a title word
    # (langfuse: 51 of 306 docs — prompt-management features, experiments — landed in "Public API",
    # "Alerts" and "Self-Hosted Deployment"). It becomes the catch-all appendix instead, which the
    # split pass partitions by page directory into named chapters.
    if big_pool:
        app = next((sid for sid, sl in slots.items() if sl.get("role") == "appendix"), None)
        if app is None:
            app = next_gap
            _open_slot(app, {
                "title": params.GAP_APPENDIX_TITLE,
                "description": _appendix_description(nofit, doc_title),
                "key_concepts": [],
            }, "gap")
            slots[app]["role"] = "appendix"
            next_gap -= 1
            stats["n_gap_chapters"] += 1
        for k in nofit:
            place[k] = app
            cands.discard(k)
        stats["n_leftover_appendix"] = len(nofit)

    # (4) leftovers → lexically nearest chapter; no overlap anywhere → the largest (catch-all) chapter.
    sizes = Counter(place.values())
    slot_toks = {sid: _slot_tokens(sl) for sid, sl in slots.items()}
    for k in sorted(k for k in cands if k not in place):
        owner = dir_owner(k)
        if owner is not None:
            place[k] = owner
            sizes[owner] += 1
            stats["n_leftover_dir_owned"] += 1
            continue
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
            **({"role": sl["role"]} if sl.get("role") else {}),
            "pinned":              False,
        })
    return {
        "kept":           [sid for sid in order if sid >= 0],
        "doc_to_chapter": place,
        "out_chapters":   out_chapters,
        "stats":          stats,
    }


def page_dirs(entries: list[dict]) -> dict[str, tuple]:
    """key -> the page's URL directory as path segments (`integrations/frameworks/langchain` ->
    ('integrations', 'frameworks')). `source_path` (the real page URL of a single-URL dump) wins
    over `url`; () when the manifest has neither."""
    out: dict[str, tuple] = {}
    for e in entries or []:
        k = e.get("key")
        if not k:
            continue
        u = (e.get("source_path") or e.get("url") or "").split("#", 1)[0].split("?", 1)[0]
        path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*/?", "", u.strip()).strip("/")
        segs = [p for p in path.split("/") if p]
        out[k] = tuple(segs[:-1])
    return out


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
    # Catalog guard: candidates piled up under one URL directory are a list of like
    # pages (providers, integrations, loaders), not orientation material.
    dir_of = lambda k: (doc_family.get(k) or k).rsplit("/", 1)[0]
    dir_docs = Counter(dir_of(k) for c in out_chapters for k in c["member_doc_keys"])
    dir_cands = Counter(dir_of(k) for _, k in cands)
    catalog_dirs = {
        d for d, n in dir_cands.items()
        if n >= params.FOUNDATIONS_CATALOG_MIN
        and dir_docs[d] <= params.FOUNDATIONS_CATALOG_MAX_SHARE * total
    }
    n_catalog = sum(1 for _, k in cands if dir_of(k) in catalog_dirs)
    cands = [(c_i, k) for c_i, k in cands if dir_of(k) not in catalog_dirs]
    stats = {
        "n_foundations": 0, "foundations_candidates": len(cands),
        "foundations_catalog_excluded": n_catalog,
    }
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


def harmonize_catalog_dirs(
    chapters: list[dict], key_dir: dict[str, tuple], assignments: dict[str, list[dict]],
) -> tuple[list[dict], dict]:
    """Pull the pages of a catalog directory (integrations/, providers/ ...) that sit in other chapters
    into the chapter that already holds most of that directory — but only pages the assigner was NOT
    sure about for their current chapter (confidence < CATALOG_STRAY_CONF: leftover / rescued
    placements; langfuse: 0.28-0.35 mean in the chapters they were dumped in vs 0.7-0.9 for the pages
    that really belong there, e.g. a checkpointer integration inside Memory). Split parts, Foundations,
    migration and appendix chapters neither give nor take.
    Returns (chapters, {"n_catalog_rehomed": n, "catalog_dirs": [...]})."""
    locked = lambda c: c.get("origin") in ("foundations", "migration") or c.get("role") in ("foundations", "appendix")
    by_dir: dict[tuple, list[tuple[int, str]]] = defaultdict(list)
    for ci, c in enumerate(chapters):
        for k in c["member_doc_keys"]:
            d = tuple(key_dir.get(k) or ())
            if d and patterns.CATALOG_DIR_RE.search("/".join(d)):
                by_dir[d].append((ci, k))
    size = [len(c["member_doc_keys"]) for c in chapters]
    gave = [0] * len(chapters)
    move: dict[str, int] = {}
    dirs_done = []
    for d, pages in sorted(by_dir.items(), key = lambda kv: -len(kv[1])):
        if len(pages) < params.CATALOG_MIN_PAGES:
            continue
        per = Counter(ci for ci, _ in pages)
        home, n_home = max(((ci, n) for ci, n in per.items() if not locked(chapters[ci])), key = lambda x: x[1], default = (None, 0))
        if home is None or n_home < params.CATALOG_MAJORITY * len(pages) or n_home == len(pages):
            continue
        n_moved = 0
        for ci, k in pages:
            if ci == home or locked(chapters[ci]) or k in move or chapters[ci].get("origin") == "split":
                continue
            conf = {a.get("chapter_idx"): a.get("confidence", 0.0) for a in (assignments.get(k) or [])}.get(
                chapters[ci].get("source_proposal_idx"), 0.0)
            if conf >= params.CATALOG_STRAY_CONF:
                continue
            if size[ci] - 1 < params.MIN_DOCS_PER_CHAPTER or gave[ci] + 1 > params.CATALOG_MAX_GIVE * len(chapters[ci]["member_doc_keys"]):
                continue
            move[k] = home
            size[ci] -= 1
            size[home] += 1
            gave[ci] += 1
            n_moved += 1
        if n_moved:
            dirs_done.append("/".join(d))
    if not move:
        return chapters, {"n_catalog_rehomed": 0, "catalog_dirs": []}
    out = []
    for ci, c in enumerate(chapters):
        keep = [k for k in c["member_doc_keys"] if k not in move]
        add = [k for k, h in move.items() if h == ci]
        out.append({**c, "member_doc_keys": keep + add})
    return _renumber(out), {"n_catalog_rehomed": len(move), "catalog_dirs": dirs_done}


def _home_dir(chapter: dict, key_dir: dict[str, tuple]) -> tuple | None:
    cnt = Counter(tuple((key_dir.get(k) or ())[:2]) for k in chapter["member_doc_keys"])
    cnt.pop((), None)
    cnt = Counter({d: n for d, n in cnt.items() if len(d) == 2})
    if not cnt:
        return None
    d, n = cnt.most_common(1)[0]
    return d if n >= 3 else None


def merge_micro_chapters(chapters: list[dict], key_dir: dict[str, tuple]) -> tuple[list[dict], dict]:
    """Merge a chapter of fewer than MICRO_MERGE_MAX_DOCS pages into the chapter that owns the directory
    most of its pages live in (never Foundations / migration / appendix / gap, never a split part).
    Pages that fit no chapter's directory stay in their own small chapter. Returns
    (chapters, {"n_micro_merged": n, "micro_merged": [titles]})."""
    total = sum(len(c["member_doc_keys"]) for c in chapters)
    locked = lambda c: c.get("origin") in ("foundations", "migration", "gap", "split") or c.get("role") in ("foundations", "appendix")
    corpus_dirs = Counter(tuple((key_dir.get(k) or ())[:2]) for c in chapters for k in c["member_doc_keys"])
    trunk = {d for d, n in corpus_dirs.items() if n > params.MICRO_MERGE_TRUNK_SHARE * total}      # the site's trunk says nothing
    homes = [None if locked(c) else _home_dir(c, key_dir) for c in chapters]
    homes = [None if h in trunk else h for h in homes]
    merged: dict[int, int] = {}
    size = [len(c["member_doc_keys"]) for c in chapters]
    for i, c in enumerate(chapters):
        if locked(c) or size[i] >= params.MICRO_MERGE_MAX_DOCS or not c["member_doc_keys"]:
            continue
        cnt = Counter(tuple((key_dir.get(k) or ())[:2]) for k in c["member_doc_keys"])
        best, best_n = None, 0
        for j, h in enumerate(homes):
            if j == i or h is None or j in merged or size[j] < params.MICRO_MERGE_MAX_DOCS:
                continue
            if cnt.get(h, 0) > best_n and size[j] + size[i] <= params.MICRO_MERGE_MAX_SHARE * total:
                best, best_n = j, cnt[h]
        if best is not None and best_n >= params.MICRO_MERGE_DIR_SHARE * len(c["member_doc_keys"]):
            merged[i] = best
            size[best] += size[i]
    if not merged:
        return chapters, {"n_micro_merged": 0, "micro_merged": []}
    out = []
    for i, c in enumerate(chapters):
        if i in merged:
            continue
        extra = [k for m, t in merged.items() if t == i for k in chapters[m]["member_doc_keys"]]
        titles = [chapters[m]["title"] for m, t in merged.items() if t == i]
        c2 = {**c, "member_doc_keys": c["member_doc_keys"] + extra}
        if titles:
            c2["description"] = ((c.get("description") or "").rstrip() + " Also covers: " + "; ".join(titles) + ".").strip()
        out.append(c2)
    return _renumber(out), {"n_micro_merged": len(merged), "micro_merged": [chapters[m]["title"] for m in merged]}


def fold_micro_chapters(chapters: list[dict]) -> tuple[list[dict], dict]:
    """Chapters of fewer than MICRO_MAX_DOCS pages whose title names orientation / support material
    (a proposed "Foundations", "Quickstart", "Troubleshooting and FAQ" ...) belong in the Foundations
    chapter that collects exactly such pages — langfuse ended with "Langfuse Foundations" (3 pages)
    beside the automatic "Foundations and Diagnostics", plus a 4-page Quickstart and a 3-page FAQ.
    No-op without a Foundations chapter. Returns (chapters, {"n_micro_folded": n})."""
    target = next((i for i, c in enumerate(chapters) if c.get("origin") == "foundations"), None)
    if target is None:
        return chapters, {"n_micro_folded": 0}
    fold = [
        i for i, c in enumerate(chapters)
        if i != target and c.get("origin") not in ("foundations", "migration")
        and len(c["member_doc_keys"]) < params.MICRO_MAX_DOCS
        and patterns.FRONT_MATTER_TITLE_RE.search(c.get("title") or "")
        and not patterns.MIGRATION_PAGE_RE.search(c.get("title") or "")
    ]
    if not fold:
        return chapters, {"n_micro_folded": 0}
    out = [dict(c) for c in chapters]
    for i in fold:
        out[target]["member_doc_keys"] = out[target]["member_doc_keys"] + out[i]["member_doc_keys"]
    out = [c for i, c in enumerate(out) if i not in set(fold)]
    return _renumber(out), {"n_micro_folded": len(fold)}


def is_migration_page(key: str, title: str | None = None) -> bool:
    """True when a page's slug or title says it is a migration / upgrade / changelog page."""
    text = re.sub(r"[-_./]+", " ", f"{key_stem(key)} {title or ''}")
    return bool(patterns.MIGRATION_PAGE_RE.search(text))


def pull_migration_pages(
    out_chapters: list[dict], doc_title: dict[str, str],
) -> tuple[list[dict], dict]:
    """Collect migration / upgrade / changelog pages scattered over topical chapters into ONE
    migration chapter (order_chapters pins it last by its title). The target is the plan's existing
    migration chapter (largest, when several); without one, a new chapter is made when at least
    MIGRATION_MIN_DOCS such pages exist and they are at most MIGRATION_MAX_SHARE of the corpus.
    Chapters emptied by the move vanish. Pure + deterministic. Returns (chapters, stats)."""
    stats = {"n_migration_pulled": 0, "migration_chapter": None}
    trailing = domains.dd.planner.nodes.order_chapters.patterns.TRAILING_RE
    total = sum(len(c["member_doc_keys"]) for c in out_chapters)
    if not total:
        return out_chapters, stats
    existing = [
        i for i, c in enumerate(out_chapters)
        if c.get("origin") != "foundations" and c.get("role") not in ("foundations", "appendix")
        and trailing.search(c.get("title") or "")
    ]
    target = max(existing, key = lambda i: (len(out_chapters[i]["member_doc_keys"]), -i)) if existing else None
    pulled = {
        k: ci for ci, c in enumerate(out_chapters) if ci not in existing
        for k in c["member_doc_keys"] if is_migration_page(k, doc_title.get(k))
    }
    if not pulled:
        return out_chapters, stats
    if target is None and not (
        params.MIGRATION_MIN_DOCS <= len(pulled) <= params.MIGRATION_MAX_SHARE * total
    ):
        return out_chapters, stats
    chapters = [dict(c, member_doc_keys = list(c["member_doc_keys"])) for c in out_chapters]
    for k, ci in pulled.items():
        chapters[ci]["member_doc_keys"].remove(k)
    if target is None:
        taken = {(c.get("title") or "").casefold() for c in chapters}
        title = params.MIGRATION_TITLE
        chapters.append({
            "title":               title if title.casefold() not in taken else f"{title} ({len(taken)})",
            "description":         params.MIGRATION_DESCRIPTION,
            "key_concepts":        [],
            "member_doc_keys":     [],
            "n_member_docs":       0,
            "order":               len(chapters) + 1,
            "source_proposal_idx": None,
            "origin":              "migration",
            "pinned":              False,
        })
        target = len(chapters) - 1
    chapters[target]["member_doc_keys"] = sorted(set(chapters[target]["member_doc_keys"]) | set(pulled))
    chapters = [c for c in chapters if c["member_doc_keys"]]
    stats["n_migration_pulled"] = len(pulled)
    stats["migration_chapter"] = out_chapters[target]["title"] if target < len(out_chapters) else params.MIGRATION_TITLE
    return _renumber(chapters), stats


def split_floor(n_docs: int, n_parts: int | None = None) -> int:
    """Smallest group a split may produce: MIN_DOCS_PER_CHAPTER, or 15% of the chapter — and, when the
    number of parts is known, never more than SPLIT_FLOOR_PART_SHARE of the average part (so k parts
    can exist: 6 parts of a 115-page chapter need a floor of 11, not 18)."""
    base = max(params.MIN_DOCS_PER_CHAPTER, math.ceil(0.15 * n_docs))
    if n_parts and n_parts >= 2:
        return max(params.MIN_DOCS_PER_CHAPTER, min(base, int(params.SPLIT_FLOOR_PART_SHARE * n_docs / n_parts)))
    return base


def plan_splits(out_chapters: list[dict], max_chapters: int = 30) -> list[tuple[int, int]]:
    """[(chapter position, n_parts)] for chapters over SPLIT_MAX_SHARE of the corpus, and for
    a catch-all appendix over GAP_SPLIT_MIN_SHARE (its parts become ordinary chapters)."""
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
        if c.get("role") == "appendix":
            if n >= params.GAP_SPLIT_MIN_DOCS and share > params.GAP_SPLIT_MIN_SHARE:
                # as many parts as fit under the chapter cap (the appendix itself is one chapter already)
                k = min(
                    params.GAP_SPLIT_MAX_PARTS, max(2, round(n / params.GAP_SPLIT_DOCS_PER_PART)),
                    max_chapters - n_chapters + 1,
                )
                if k >= 2:
                    out.append((i, k))
                    n_chapters += k - 1
            continue
        if share <= params.SPLIT_MAX_SHARE or n <= params.SPLIT_MEDIAN_RATIO * median:
            continue
        k = min(
            params.SPLIT_MAX_PARTS,
            max(2, math.ceil(share / params.SPLIT_MAX_SHARE), round(n / params.OVERSIZE_DOCS_PER_PART)),
            n // params.MIN_DOCS_PER_CHAPTER,
            max_chapters - n_chapters + 1,
        )
        if k >= 2:
            out.append((i, k))
            n_chapters += k - 1
    return out


def key_stem(key: str) -> str:
    """The URL slug inside a corpus key: `ingestion/<fw>/pages/0336-<slug>.md` -> `<slug>`."""
    m = patterns.KEY_STEM_RE.search(key or "")
    return m.group(1) if m else (key or "")


def _slug_words(stems: list[str]) -> list[set[str]]:
    """Words of each slug with the prefix shared by ALL of them (the dump's host/slug) removed;
    digits and one-letter tokens dropped."""
    pre = os.path.commonprefix(stems) if len(stems) > 1 else ""
    cut = pre.rfind("-") + 1 if "-" in pre else 0
    return [
        {w for w in re.split(r"[-_./]+", s[cut:].lower()) if len(w) > 1 and not w.isdigit()}
        for s in stems
    ]


def _unit_label(title: str) -> str:
    return (title or "").split(" — ")[0].strip() or "(untitled)"


def build_split_units(
    member_keys: list[str],
    *,
    anchored_family: dict[str, str],
    doc_title: dict[str, str],
    distillates: dict[str, dict],
    key_idx: dict[str, int],
    key_dir: dict[str, tuple] | None = None,
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
        roles = Counter((distillates.get(k) or {}).get("role") for k in keys)
        roles.pop(None, None)
        units.append({
            "keys":    keys,
            "label":   _unit_label(doc_title.get(keys[0], "")),
            "summary": summ[:params.SPLIT_UNIT_SUMMARY_CHARS],
            "slug":    key_stem(keys[0]),
            "role":    roles.most_common(1)[0][0] if roles else None,
            "dir":     tuple((key_dir or {}).get(keys[0]) or ()),
        })
    units.sort(key = lambda u: (key_idx.get(u["keys"][0], 1 << 30), u["keys"][0]))
    for i, u in enumerate(units):
        u["id"] = i
    return units


def validate_split(
    parts: list[dict], units: list[dict], other_titles: set[str], max_parts: int | None = None,
) -> tuple[bool, str]:
    """Check an LLM split proposal: parts = [{title, description, unit_ids}].
    Pure. Returns (ok, reason) — the reason is fed back to the model on repair."""
    n_docs = sum(len(u["keys"]) for u in units)
    limit = max_parts or params.SPLIT_MAX_PARTS
    if not (2 <= len(parts) <= limit):
        return False, f"need between 2 and {limit} groups, got {len(parts)}"
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
    floor = split_floor(n_docs, len(parts))
    titles: set[str] = set()
    for p in parts:
        size = sum(len(units[u]["keys"]) for u in p["unit_ids"])
        if size < floor:
            return False, f"group {p.get('title')!r} has only {size} docs (minimum {floor})"
        t = " ".join((p.get("title") or "").split())
        if not (1 <= len(t.split()) <= 8):
            return False, f"title {t!r} must be 1-8 words"
        if t.casefold() in params.GENERIC_TITLES:
            return False, f"title {t!r} is a generic name — use a concrete topic"
        if t.casefold() in titles or t.casefold() in other_titles:
            return False, f"title {t!r} is not unique among chapters"
        titles.add(t.casefold())
    return True, ""


def normalize_split(parts: list[dict], units: list[dict]) -> list[dict]:
    """Repair an LLM split instead of rejecting it (deepagents: a 41-doc catch-all chapter
    shipped unsplit after two rejections "units not placed in any group" / "group has only 3
    docs"): duplicate and unknown unit ids are dropped, empty groups vanish, units the model
    forgot join the group of their nearest placed unit (units are in source order, so
    neighbours are related), and groups under the size floor merge into the group with the
    nearest centre. Two groups are never merged away — those, and bad titles, still go back to
    the model through validate_split."""
    ids = {u["id"] for u in units}
    placed: set[int] = set()
    out: list[dict] = []
    for p in parts:
        uids = []
        for u in p.get("unit_ids") or []:
            if u in ids and u not in placed:
                placed.add(u)
                uids.append(u)
        if uids:
            out.append({**p, "unit_ids": uids})
    if len(out) < 2:
        return out
    owner = {u: gi for gi, p in enumerate(out) for u in p["unit_ids"]}
    for u in sorted(ids - placed):
        nearest = min(owner, key = lambda x: (abs(x - u), x))
        owner[u] = owner[nearest]
        out[owner[u]]["unit_ids"].append(u)
    n_docs = sum(len(u["keys"]) for u in units)
    floor = split_floor(n_docs)
    size = lambda p: sum(len(units[u]["keys"]) for u in p["unit_ids"])
    centre = lambda p: sum(p["unit_ids"]) / len(p["unit_ids"])
    while len(out) > 2:
        small = min(out, key = size)
        if size(small) >= floor:
            break
        others = [p for p in out if p is not small]
        target = min(others, key = lambda p: abs(centre(p) - centre(small)))
        target["unit_ids"] = target["unit_ids"] + small["unit_ids"]
        out.remove(small)
    for p in out:
        p["unit_ids"] = sorted(p["unit_ids"])
    return out


def unit_text(unit: dict, distillates: dict[str, dict]) -> str:
    """What a unit is clustered on: its label plus the match text of its first pages."""
    texts = doc_match_texts(unit["keys"][:3], distillates)
    return " ".join([unit.get("label") or "", *texts.values()])


def unit_similarity(units: list[dict], texts: list[str]) -> list[list[float]]:
    """Pairwise page similarity for clustering: SPLIT_SIM_* weighted slug-word cosine + summary/term
    cosine + same-role bonus. Units without a slug (hand-built ones) fall back to the text cosine."""
    n = len(units)
    words = domains.dd.planner.nodes.chapter_propose.domain.match_words
    t_ws = [words(t) for t in texts]
    t_idf, t_unseen = _word_idf(t_ws)
    t_vecs = [_word_vec(w, t_idf, t_unseen) for w in t_ws]
    use_slug = all(u.get("slug") for u in units)
    if use_slug:
        s_ws = _slug_words([u["slug"] for u in units])
        s_idf, s_unseen = _word_idf(s_ws)
        s_vecs = [_word_vec(w, s_idf, s_unseen) for w in s_ws]
    use_dir = sum(1 for u in units if u.get("dir")) >= 0.5 * n
    dirs = [u.get("dir") or () for u in units]
    w_s = params.SPLIT_SIM_SLUG_WEIGHT if use_slug else 0.0
    w_t = params.SPLIT_SIM_TEXT_WEIGHT if use_slug else 1.0 - params.SPLIT_SIM_ROLE_WEIGHT
    w_r = params.SPLIT_SIM_ROLE_WEIGHT
    sim = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = w_t * _cosine(t_vecs[i], t_vecs[j])
            if use_slug:
                s += w_s * _cosine(s_vecs[i], s_vecs[j])
            if use_dir:
                a, b = dirs[i], dirs[j]
                common = 0
                for x, y in zip(a, b):
                    if x != y:
                        break
                    common += 1
                s += params.SPLIT_SIM_DIR_WEIGHT * common / max(len(a), len(b), 1)
            if units[i].get("role") and units[i].get("role") == units[j].get("role"):
                s += w_r
            sim[i][j] = sim[j][i] = s
    return sim


def cluster_units(
    units: list[dict], texts: list[str], max_parts: int, floor: int,
) -> list[list[int]] | None:
    """Partition `units` into 2..max_parts groups of at least `floor` pages each, by
    average-linkage clustering (similarity: unit_similarity). The smallest group below the floor
    always merges into its most similar neighbour first (so no tiny straggler group exists and
    no early blob swallows everything); once all reach the floor, the most similar pair merges
    until max_parts remain. No merge may create a group above SPLIT_CLUSTER_MAX_SHARE of the
    chapter unless nothing else is possible. Deterministic. None when fewer than 2 groups
    survive (the caller falls back to asking the model)."""
    n = len(units)
    if n < 2 or len(texts) != n:
        return None
    base = unit_similarity(units, texts)
    size = {i: len(units[i]["keys"]) for i in range(n)}
    weight = {i: 1 for i in range(n)}
    members = {i: [i] for i in range(n)}
    sim: dict[int, dict[int, float]] = {i: {j: base[i][j] for j in range(n) if j != i} for i in range(n)}
    cap = math.ceil(params.SPLIT_CLUSTER_MAX_SHARE * sum(size.values()))

    def merge(a: int, b: int) -> None:
        wa, wb = weight[a], weight[b]
        for c in members:
            if c in (a, b):
                continue
            s = (wa * sim[a][c] + wb * sim[b][c]) / (wa + wb)
            sim[a][c] = sim[c][a] = s
            del sim[c][b]
        del sim[a][b]
        del sim[b]
        members[a] += members.pop(b)
        size[a] += size.pop(b)
        weight[a] = wa + wb
        del weight[b]

    while len(members) > 1:
        small = [c for c in members if size[c] < floor]
        if small:
            c = min(small, key = lambda x: (size[x], x))
            others = [o for o in members if o != c]
            fit = [o for o in others if size[c] + size[o] <= cap] or others
            o = max(fit, key = lambda x: (sim[c][x], -size[x], -x))
            a, b = min(c, o), max(c, o)
        elif len(members) > max_parts:
            ids = sorted(members)
            pairs = [(x, y) for ix, x in enumerate(ids) for y in ids[ix + 1:]]
            fit = [p for p in pairs if size[p[0]] + size[p[1]] <= cap] or pairs
            a, b = max(fit, key = lambda p: (sim[p[0]][p[1]], -(size[p[0]] + size[p[1]]), -p[0]))
        else:
            break
        merge(a, b)
    if len(members) < 2:
        return None
    return sorted((sorted(m) for m in members.values()), key = lambda m: m[0])


def fallback_group_titles(
    groups: list[list[int]], texts: list[str], taken: set[str],
) -> list[dict]:
    """Deterministic {title, description} per group from its most distinctive words, used when
    the model cannot name a group (a split must still apply)."""
    words = domains.dd.planner.nodes.chapter_propose.domain.match_words
    ws = [words(t) for t in texts]
    idf, unseen = _word_idf(ws)
    used = {t.casefold() for t in taken}
    out = []
    for g in groups:
        cnt = Counter(w for i in g for w in ws[i])
        ranked = sorted(cnt, key = lambda w: (-cnt[w] * idf.get(w, unseen), w))
        top = ranked[:max(params.SPLIT_NAME_FALLBACK_TERMS + 3, 6)]
        n_words = params.SPLIT_NAME_FALLBACK_TERMS
        title = " ".join(w.title() for w in top[:n_words])
        while title.casefold() in used and n_words < len(top):
            n_words += 1
            title = " ".join(w.title() for w in top[:n_words])
        used.add(title.casefold())
        out.append({
            "title": title or "Additional Topics",
            "description": f"Pages covering {', '.join(top[:5])}." if top else "Related pages.",
        })
    return out


def apply_split(chapter: dict, parts: list[dict], units: list[dict]) -> list[dict]:
    """Replace one chapter by its validated parts (order within the chapter by source position)."""
    out = []
    for j, p in enumerate(parts):
        keys = sorted(k for u in p["unit_ids"] for k in units[u]["keys"])
        out.append({
            **{k: v for k, v in chapter.items() if k != "role"},   # a split-off part is no appendix
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
    key_dir: dict[str, tuple] | None = None,
) -> dict:
    """The whole deterministic selection: greedy coverage → prune → consolidate
    (incl. anchored-family harmonization) → Foundations chapter. Shared by the
    node and the offline replay (scripts/dd/replay_select.py), so what is
    measured is what ships. The LLM oversize-split runs after this, in the node."""
    pinned = detect_pinned_indices(proposals, seeds)
    try:
        affinity = doc_affinity(proposals, doc_match_texts(list(assignments), distillates))
    except Exception:
        affinity = {}        # ties then fall back to proposal order, as before
    selected, doc_to_chapter = greedy_select(
        proposals = proposals, assignments = assignments, pinned_indices = pinned,
        affinity = affinity,
    )
    final = prune_and_finalize_selection(
        selected = selected, doc_to_chapter = doc_to_chapter, assignments = assignments,
        pinned = pinned, proposals = proposals, affinity = affinity,
    )
    cons = consolidate_placements(
        kept = final["kept"], doc_to_chapter = final["doc_to_chapter"],
        assignments = assignments, proposals = proposals,
        doc_family = doc_family, doc_title = doc_title, anchored_family = anchored_family,
        affinity = affinity, key_dir = key_dir,
    )
    doc_role = {k: (d or {}).get("role") for k, d in (distillates or {}).items()}
    chapters, f_stats = apply_foundations(
        out_chapters = cons["out_chapters"], doc_role = doc_role,
        doc_family = doc_family, key_idx = key_idx,
    )
    chapters, mf_stats = fold_micro_chapters(chapters)
    chapters, m_stats = pull_migration_pages(chapters, doc_title)
    stats = {**cons["stats"], **f_stats, **mf_stats, **m_stats}
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
