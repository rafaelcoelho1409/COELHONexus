"""plan_write — pure helpers (title-case, slugify, trim, hydrate +
sanitize chapters, manifest hash, outline loader)."""
from __future__ import annotations

import json
from hashlib import sha256

import numpy as np

from .params import (
    DESCRIPTION_MAX_CHARS,
    SLUG_MAX_WORDS,
    TITLE_LOWERCASE,
    TITLE_MAX_WORDS,
    TITLE_UPPERCASE,
)
from .patterns import SLUG_RE
from .versions import PROMPT_VERSION


def load_outline(text: str) -> dict:
    """Parse the JSON outline written by chapter_select. Tolerates an
    empty/malformed blob (returns ``{}`` so downstream can render an
    empty plan rather than crash mid-run)."""
    try:
        return json.loads(text or "") or {}
    except Exception:
        return {}


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
        if low in TITLE_UPPERCASE:
            out.append(low.upper())
            continue
        if low in TITLE_LOWERCASE and 0 < i < len(words) - 1:
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
    parts = [p for p in SLUG_RE.sub("-", low).split("-") if p]
    return "-".join(parts[:SLUG_MAX_WORDS]) or "chapter"


def trim_description(desc: str) -> str:
    cleaned = " ".join((desc or "").strip().split())
    if len(cleaned) <= DESCRIPTION_MAX_CHARS:
        return cleaned
    cut = cleaned[: DESCRIPTION_MAX_CHARS - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def build_cluster_to_keys(
    refined_assignments: np.ndarray, keys: list[str],
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
        if len(words) > TITLE_MAX_WORDS:
            title = " ".join(words[:TITLE_MAX_WORDS])

        sanitized.append({
            "title":              title,
            "description":        trim_description(
                ch.get("description") or "",
            ),
            "member_cluster_ids": member_ids,
            "sources":            sorted(sources),
            "n_sources":          len(sources),
        })

    # Re-number `order` to 1..N contiguous + assign stable slug ids.
    for i, ch in enumerate(sanitized, start = 1):
        ch["order"] = i
        ch["id"] = f"ch-{i:02d}-{slugify(ch['title'])}"

    return sanitized, dropped


def compute_manifest_hash(
    chapter_plan_ref: str, schema_version: str,
) -> str:
    """Hash inputs determining the plan. PROMPT_VERSION folded in so a
    prompt update invalidates cache without an outline change."""
    payload = (
        f"chapter_plan={chapter_plan_ref}|"
        f"schema={schema_version}|prompt={PROMPT_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]
