"""order_chapters — pure helpers (permutation check, Borda aggregate,
foundational prefix rule, outline loader). Prompt builder lives in
prompts.py; Pydantic schema in schemas.py. LLM output decoding is
`chat_structured_async` — no manual JSON parse needed here anymore."""
from __future__ import annotations
from . import params, patterns

import difflib
import json
import math
import random
import re
from collections import Counter


def load_outline(text: str) -> dict:
    """Parse chapter_select outline; normalizes nested {"outline": {"chapters": [...]}} to flat {"chapters": [...]} — without this, plan_write ships 0 chapters (browser-use 2026-06-06 regression)."""
    try:
        d = json.loads(text or "") or {}
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    # New nested form: chapters live under d["outline"]["chapters"].
    inner = d.get("outline")
    if isinstance(inner, dict) and isinstance(inner.get("chapters"), list):
        d = dict(d)
        d["chapters"] = inner["chapters"]
    return d


def is_foundational(title: str) -> bool:
    """install/setup/cli/quickstart — must anchor at position 0."""
    if not title:
        return False
    return bool(patterns.FOUNDATIONAL_RE.search(title))


def borda_aggregate(
    rankings: list[list[int]], n_chapters: int,
) -> list[int]:
    """Borda count across rank permutations. Score = sum(n - pos - 1) per
    chapter. Ties broken stably by earliest-ranking appearance (preserves
    bandit primary-deployment opinion). Empty → identity permutation."""
    if not rankings:
        return list(range(n_chapters))
    scores = [0] * n_chapters
    first_appearance = [n_chapters] * n_chapters
    for r_idx, ranking in enumerate(rankings):
        for pos, chapter_idx in enumerate(ranking):
            if 0 <= chapter_idx < n_chapters:
                scores[chapter_idx] += (n_chapters - pos - 1)
                if r_idx < first_appearance[chapter_idx]:
                    first_appearance[chapter_idx] = r_idx
    return sorted(
        range(n_chapters),
        key = lambda i: (-scores[i], first_appearance[i]),
    )


def source_order_ranking(chapters: list[dict], key_idx: dict[str, int]) -> list[int] | None:
    """Chapter indices ordered by where their pages sit in the source docs (mean
    manifest `idx` of the members). Chapters with no known page go last, in
    their current order. None when no chapter has a known page."""
    means: list[float | None] = []
    for ch in chapters:
        idxs = [key_idx[k] for k in (ch.get("member_doc_keys") or []) if k in key_idx]
        means.append(sum(idxs) / len(idxs) if idxs else None)
    if all(m is None for m in means):
        return None
    return sorted(
        range(len(chapters)),
        key = lambda i: (means[i] is None, means[i] if means[i] is not None else 0.0, i),
    )


def source_order_informative(entries: list[dict]) -> bool:
    """True when the manifest's `idx` order is the docs' own sequence. False for one page
    (single-URL dump) and for an alphabetical listing: the share of neighbouring pages whose
    URL path ascends is ≥ params.SOURCE_ORDER_ALPHA_MAX."""
    ents = sorted((e for e in entries or [] if e.get("key")), key = lambda e: int(e.get("idx") or 0))
    paths = [(e.get("source_path") or e.get("url") or "").split("#", 1)[0].casefold() for e in ents]
    changes = [(a, b) for a, b in zip(paths, paths[1:]) if a != b]
    if len(set(paths)) < params.SOURCE_ORDER_MIN_PATHS or not changes:
        return False
    ascending = sum(1 for a, b in changes if b >= a)
    return ascending / len(changes) < params.SOURCE_ORDER_ALPHA_MAX


def chapter_composition(chapters: list[dict], distillates: dict[str, dict]) -> list[dict | None]:
    """Per chapter: {n_docs, reference_pct} from doc_distill `role` (API/reference pages vs
    guides, tutorials and explanations) — what the orderer needs to tell a reference chapter
    from a teaching one. None where no member has a distillate."""
    out: list[dict | None] = []
    for ch in chapters:
        keys = ch.get("member_doc_keys") or []
        roles = [(distillates.get(k) or {}).get("role") for k in keys if k in distillates]
        if not roles:
            out.append(None)
            continue
        out.append({
            "n_docs": len(keys),
            "reference_pct": round(100 * sum(1 for r in roles if r == "reference") / len(roles)),
        })
    return out


def chapter_labels(chapters: list[dict]) -> list[str]:
    """One unique display label per chapter (its title; a repeated title gets " (2)" …)."""
    seen: Counter = Counter()
    out = []
    for i, ch in enumerate(chapters):
        t = " ".join(str(ch.get("title") or f"Chapter {i}").split())
        seen[t.casefold()] += 1
        out.append(t if seen[t.casefold()] == 1 else f"{t} ({seen[t.casefold()]})")
    return out


def _norm_title(s: str) -> str:
    s = re.sub(r"^\s*(\[\d+\]|\d+[.)])\s*", "", str(s))
    return " ".join(s.strip().strip("'\"*`").casefold().split())


def titles_to_order(raw: list, labels: list[str]) -> list[int] | None:
    """Map the titles a model returned back to chapter indices: exact (case-insensitive) match,
    else the closest label above params.TITLE_MATCH_CUTOFF; unknown and repeated titles are
    skipped. Chapters the model left out follow in their current order — but only when it
    named at least params.TITLE_MATCH_MIN_SHARE of them, else None (a sample that dropped half
    the chapters carries no ordering information)."""
    by = {_norm_title(l): i for i, l in enumerate(labels)}
    seen: set[int] = set()
    order: list[int] = []
    for r in raw or []:
        k = _norm_title(r)
        i = by.get(k)
        if i is None:
            m = difflib.get_close_matches(k, list(by), n = 1, cutoff = params.TITLE_MATCH_CUTOFF)
            i = by[m[0]] if m else None
        if i is not None and i not in seen:
            seen.add(i)
            order.append(i)
    if len(order) < math.ceil(params.TITLE_MATCH_MIN_SHARE * len(labels)):
        return None
    return order + [i for i in range(len(labels)) if i not in seen]


def shuffled_indices(n: int, seed: int) -> list[int]:
    """Presentation order of the chapters for one sample (deterministic per sample): a model
    leans on list position, so each of the N samples sees a different arrangement and the
    Borda aggregate cancels that lean."""
    idx = list(range(n))
    random.Random(1009 * (seed + 1) + n).shuffle(idx)
    return idx


def pin_trailing_last(order: list[int], chapters: list[dict]) -> tuple[list[int], list[int]]:
    """Migration / upgrade / changelog chapters are for readers who know an older version: they
    close the book, ahead of the appendix. Foundations, appendix and gap chapters are left to
    their own pins. Returns (order, pinned)."""
    pinned = [
        i for i in order
        if (chapters[i] or {}).get("role") not in ("foundations", "appendix")
        and (chapters[i] or {}).get("origin") not in ("foundations", "gap")
        and patterns.TRAILING_RE.search((chapters[i] or {}).get("title") or "")
    ]
    if not pinned:
        return list(order), []
    return [i for i in order if i not in set(pinned)] + pinned, pinned


def pin_foundations_first(order: list[int], chapters: list[dict]) -> tuple[list[int], list[int]]:
    """The Foundations chapter (role == "foundations", built by chapter_select
    from overview + cross-cutting pages) opens the book. Returns (order, pinned)."""
    pinned = [i for i in order if (chapters[i] or {}).get("role") == "foundations"]
    if not pinned:
        return list(order), []
    return pinned + [i for i in order if i not in set(pinned)], pinned


def pin_appendix_last(order: list[int], chapters: list[dict]) -> tuple[list[int], list[int]]:
    """The generic catch-all chapter (role == "appendix", built by chapter_select for
    pages that fit nowhere and share no page title) closes the book.
    Returns (order, pinned)."""
    pinned = [i for i in order if (chapters[i] or {}).get("role") == "appendix"]
    if not pinned:
        return list(order), []
    return [i for i in order if i not in set(pinned)] + pinned, pinned


def apply_foundational_prefix_rule(
    order: list[int],
    chapters: list[dict],
) -> tuple[list[int], list[int]]:
    """Pin foundational chapters to the FRONT, preserving their relative
    Borda order. Returns (new_order, promoted_indices) for telemetry."""
    # Foundations / appendix chapters are placed by their own pins; a "gap" chapter
    # named after an arbitrary page title (fastmcp: `…-quickstart`) is not a quickstart.
    foundational_indices = [
        i for i, ch in enumerate(chapters)
        if ch.get("origin") not in ("gap", "foundations")
        and ch.get("role") not in ("foundations", "appendix")
        and is_foundational(ch.get("title", ""))
    ]
    if not foundational_indices:
        return list(order), []
    foundational_set = set(foundational_indices)
    fnd_in_order = [i for i in order if i in foundational_set]
    rest = [i for i in order if i not in foundational_set]
    return fnd_in_order + rest, fnd_in_order


def load_chapter_order(text: str) -> list[int] | None:
    """Convenience loader for plan_write. None on malformed blob."""
    try:
        payload = json.loads(text)
    except Exception:
        return None
    order = payload.get("order")
    if not isinstance(order, list):
        return None
    try:
        return [int(x) for x in order]
    except (ValueError, TypeError):
        return None
