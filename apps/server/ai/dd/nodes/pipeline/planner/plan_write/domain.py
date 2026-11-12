"""plan_write — pure helpers (title-case, slugify, trim, hydrate +
sanitize chapters, manifest hash, outline loader, pipeline-health rollup)."""
from __future__ import annotations
from . import params, patterns, versions

import json
import hashlib

from numpy import ndarray



def chapter_size_profile(sizes: list[int]) -> dict:
    """max / median chapter size, the largest chapter's share of all sources, and an `oversized_chapter` flag (max ≥ 20 docs AND ≥ 2.5× the median). Informational only."""
    sizes = sorted(int(x) for x in sizes if isinstance(x, (int, float)))
    if not sizes:
        return {}
    mx = sizes[-1]
    mid = len(sizes) // 2
    median = sizes[mid] if len(sizes) % 2 else (sizes[mid - 1] + sizes[mid]) / 2
    total = sum(sizes)
    return {
        "chapter_size_max":          mx,
        "chapter_size_median":       median,
        "chapter_largest_share_pct": round(100.0 * mx / total, 1) if total else 0.0,
        "oversized_chapter":         bool(mx >= 20 and median > 0 and mx >= 2.5 * median),
    }


def pipeline_health(state: dict) -> dict:
    """Roll the per-node fallback/degradation signals into the plan's stats so a
    'done' plan that silently ran on deterministic fallbacks (generic chapter
    titles, lopsided buckets) is distinguishable from a high-quality one without
    MinIO archaeology. Quality-affecting fallbacks and unresolved design signals
    mark the plan for review before Synth can consume it."""
    dd = state.get("doc_distill_stats") or {}
    ot = state.get("off_topic_stats") or {}
    pr = state.get("propose_stats") or {}
    asg = state.get("assign_stats") or {}
    ordc = state.get("order_chapters_stats") or {}
    sel = state.get("select_stats") or {}
    design = state.get("chapter_design_stats") or {}
    design_errors = design.get("designer_errors") or []
    design_review_flags = design.get("designer_review_flags") or []

    def _pct(num, den):
        return round(100.0 * num / den, 1) if den else 0.0

    dd_n = dd.get("n_distilled") or dd.get("n_files") or 0
    asg_n = asg.get("n_assigned") or asg.get("n_docs") or 0
    health = {
        "off_topic_llm_errors":       ot.get("llm_errors", ot.get("llm_err", 0)),
        "doc_distill_fallback_pct":   _pct(dd.get("n_fallback", 0), dd_n),
        "doc_distill_failure_reasons": dd.get("failure_reasons") or {},
        "chapter_propose_fallback_used": bool(pr.get("fallback_used", False)),
        "chapter_propose_samples_valid": int(pr.get("n_samples_valid", 0)),
        "chapter_propose_n_proposals": pr.get("n_proposals", 0),
        "chapter_assign_fallback_pct": _pct(asg.get("n_fallback", 0), asg_n),
        "chapter_assign_rescued":     asg.get("n_rescued", 0),
        "order_chapters_valid_samples": ordc.get("n_samples", 0),
    }
    # Single headline flag: was any structural stage degraded to its
    # deterministic fallback? (the thing that produces a throwaway plan)
    health["degraded"] = bool(
        pr.get("fallback_used", False)
        or _pct(dd.get("n_fallback", 0), dd_n) >= 40.0
        or _pct(asg.get("n_fallback", 0), asg_n) >= 50.0
        or (0 < sel.get("n_chapters_out", 99) < 3)   # collapsed plan
    )
    health["chapter_select_n_chapters"] = sel.get("n_chapters_out", 0)
    # Informational chapter-size profile (never feeds `degraded`): one chapter
    # swallowing a quarter of the corpus is visible here before Synth spends an
    # hour on it (asyncio: a 26-page catch-all cited 12 of its 26 sources).
    health.update(chapter_size_profile(sel.get("chapter_sizes") or []))
    # Docs the plan lost between assignment and plan — must be 0. `n_unassigned`
    # in the plan stats only compares against docs that reached plan_write, so
    # it reported 0 while 53 of 694 elasticsearch-python docs had vanished.
    health["chapter_select_unplaced_docs"] = max(
        int(sel.get("n_total_docs", 0)) - int(sel.get("n_assigned_docs", 0)), 0,
    )
    cons = sel.get("consolidation") or {}
    health["chapter_select_rescued_chapters"] = cons.get("n_rescued_chapters", 0)
    health["chapter_select_gap_chapters"] = cons.get("n_gap_chapters", 0)
    health["chapter_select_family_rehomed"] = (
        cons.get("n_family_consolidated", 0) + cons.get("n_rehomed_to_family", 0)
    )
    if state.get("planner_version") == "v2":
        cards = state.get("page_cards_stats") or {}
        design = state.get("chapter_design_stats") or {}
        # The v2 selector assigns only kept canonical pages. Raw pages include
        # off-topic rejects and exact duplicate aliases, so use the kept count
        # when reporting chapter coverage and degradation.
        pages = int(cards.get("n_kept") or design.get("n_pages_in") or 0)
        assigned = int(
            sel.get("n_assigned_docs")
            if "n_assigned_docs" in sel
            else design.get("n_pages_assigned") or 0
        )
        fallback_pct = _pct(cards.get("n_fallback_cards", 0), cards.get("n_kept", 0))
        health.update({
            "planner_version": "v2",
            # v2 has no separate chapter_propose node; its proposal artifact is
            # produced by chapter_design, so don't report the aliased stats as
            # a second, independent proposal fallback.
            "chapter_propose_fallback_used": False,
            "page_card_fallback_pct": fallback_pct,
            "page_card_dropped_pages": cards.get("n_dropped", 0),
            "chapter_design_fallback_used": bool(design.get("fallback_used")),
            "chapter_design_errors": len(design_errors),
            "chapter_design_review_flags": len(design_review_flags),
            "chapter_design_review_reasons": [str(flag)[:280] for flag in design_review_flags if str(flag).strip()][:8],
            "chapter_design_error_details": [str(error)[:280] for error in design_errors if str(error).strip()][:8],
            "chapter_design_recoveries": [str(item)[:200] for item in (design.get("designer_recoveries") or []) if str(item).strip()][:8],
            "chapter_design_ledger_available": design.get("ledger_available"),
            "chapter_design_embedding_model": design.get("embedding_model"),
            "chapter_design_candidate_count": design.get("n_partitions_scored", 0),
            "chapter_design_total_candidate_count": design.get("n_partitions_total_scored", design.get("n_partitions_scored", 0)),
            "chapter_design_extreme_candidates_removed": design.get("n_partitions_rejected_extreme", 0),
            "chapter_design_best_code": design.get("n_best_code", 0),
            "chapter_design_suggested_chapters": design.get("suggested_chapters"),
            "chapter_select_page_coverage_pct": _pct(assigned, pages),
            "chapter_select_unplaced_pages": max(pages - assigned, 0),
            "prerequisite_cycles_guarded": ordc.get("n_cycle_guarded_chapters", 0),
            "prerequisites_unresolved": ordc.get("n_unresolved_prerequisites", 0),
        })
        health["degraded"] = bool(
            fallback_pct >= 40.0
            or design.get("fallback_used")
            or bool(design_errors)
            or bool(design_review_flags)
            or design.get("ledger_available") is False
            or (pages and assigned < pages)
            or ordc.get("n_samples", 0) == 0 and int(sel.get("n_chapters_out", 0)) > 1
            or (pages > 15 and int(sel.get("n_chapters_out", 0)) < 3)
        )
    return health


def load_outline(text: str) -> dict:
    """Parse chapter_select outline; normalizes nested {"outline": {"chapters": [...]}} to flat {"chapters": [...]} — without this, plan_write ships 0 chapters (browser-use 2026-06-06 regression)."""
    try:
        d = json.loads(text or "") or {}
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    inner = d.get("outline")
    if isinstance(inner, dict) and isinstance(inner.get("chapters"), list):
        d = dict(d)
        d["chapters"] = inner["chapters"]
    return d


def smart_title_case(s: str) -> str:
    """Title-case that preserves common acronyms (API/CLI/SDK/...) and
    lowercases linking words (of/and/the/...). First+last words always
    capitalize. Falls through cleanly on already-Title-Case input."""
    raw = (s or "").strip()
    if not raw:
        return ""
    words = raw.split()
    out: list[str] = []
    for i, w in enumerate(words):
        low = w.lower()
        if low in params.TITLE_UPPERCASE:
            out.append(low.upper())
            continue
        if low in params.TITLE_LOWERCASE and 0 < i < len(words) - 1:
            out.append(low)
            continue
        # Preserve internal-cap words (e.g. "LangGraph", "ZeroMQ") if the
        # input is mixed-case; otherwise smart-capitalize.
        if any(c.isupper() for c in w[1:]):
            out.append(w)
        else:
            out.append(low[:1].upper() + low[1:])
    return " ".join(out)


def slugify(s: str) -> str:
    """ASCII-lowercase slug for stable chapter IDs."""
    low = (s or "").strip().lower()
    if not low:
        return "chapter"
    parts = [p for p in patterns.SLUG_RE.sub("-", low).split("-") if p]
    return "-".join(parts[:params.SLUG_MAX_WORDS]) or "chapter"


def trim_description(desc: str) -> str:
    cleaned = " ".join((desc or "").strip().split())
    if len(cleaned) <= params.DESCRIPTION_MAX_CHARS:
        return cleaned
    cut = cleaned[: params.DESCRIPTION_MAX_CHARS - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def build_cluster_to_keys(
    refined_assignments: ndarray, keys: list[str],
) -> dict[int, list[str]]:
    """Group MinIO doc keys by refined cluster_id. Noise (-1) included
    so the sanitizer can decide whether to drop it."""
    out: dict[int, list[str]] = {}
    n = min(len(keys), int(refined_assignments.shape[0]))
    for i in range(n):
        cid = int(refined_assignments[i])
        out.setdefault(cid, []).append(keys[i])
    for cid in out:
        out[cid] = sorted(set(out[cid]))
    return out


def sanitize_chapters(
    outline_chapters: list[dict],
    cluster_to_keys: dict[int, list[str]],
    *,
    preserve_metadata: bool = False,
    stable_identity: bool = False,
) -> tuple[list[dict], int]:
    """Hydrate sources + title/description cleanup + drop empty +
    re-number. Returns (chapters, n_dropped)."""
    raw_sorted = sorted(
        (c for c in outline_chapters if isinstance(c, dict)),
        key = lambda c: (c.get("order") or 999, c.get("title") or ""),
    )

    sanitized: list[dict] = []
    dropped = 0
    seen_global_keys: set[str] = set()

    for ch in raw_sorted:
        member_ids = []
        for cid in (ch.get("member_cluster_ids") or []):
            try:
                member_ids.append(int(cid))
            except (TypeError, ValueError):
                continue

        # Hydrate sources from refined assignments. Dedup across the
        # whole plan — a doc must appear in AT MOST ONE chapter, even if
        # the outline duplicated a cluster id.
        sources: list[str] = []
        for cid in member_ids:
            for key in cluster_to_keys.get(cid, []):
                if key in seen_global_keys:
                    continue
                seen_global_keys.add(key)
                sources.append(key)

        if not sources:
            dropped += 1
            continue

        title = smart_title_case(ch.get("title") or "Untitled Chapter")
        words = title.split()
        if len(words) > params.TITLE_MAX_WORDS:
            title = " ".join(words[:params.TITLE_MAX_WORDS])

        row = {
            "title":              title,
            "description":        trim_description(
                ch.get("description") or "",
            ),
            "member_cluster_ids": member_ids,
            "sources":            sorted(sources),
            "n_sources":          len(sources),
        }
        if preserve_metadata:
            for name in (
                "objective", "key_concepts", "prerequisites", "kind", "role",
                "capstone_ability", "capstone_reason", "n_best_code", "best_code_ids", "reading_minutes", "tutorial_minutes",
                "synth_eligible", "synth_exclusion_reason",
            ):
                if name in ch:
                    value = ch[name]
                    row[name] = list(value) if name in ("key_concepts", "prerequisites") and isinstance(value, list) else value
        sanitized.append(row)

    # Re-number `order` to 1..N contiguous + assign stable slug ids.
    for i, ch in enumerate(sanitized, start = 1):
        ch["order"] = i
        if stable_identity:
            identity = hashlib.sha256("\n".join(ch["sources"]).encode("utf-8")).hexdigest()[:12]
            ch["id"] = f"ch-{identity}"
        else:
            ch["id"] = f"ch-{i:02d}-{slugify(ch['title'])}"

    return sanitized, dropped


def compute_manifest_hash(
    chapter_plan_ref: str, schema_version: str,
) -> str:
    """Hash inputs determining the plan. PROMPT_VERSION folded in so a
    prompt update invalidates cache without an outline change."""
    payload = (
        f"chapter_plan={chapter_plan_ref}|"
        f"schema={schema_version}|prompt={versions.PROMPT_VERSION}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
