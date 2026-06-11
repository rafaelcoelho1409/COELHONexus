"""Pure parsing of HN Algolia /search responses — Functional Core.

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no clocks, no logging,
no mutable globals (the regexes live in patterns.py). Deterministic.

Robustness: malformed entries are silently dropped (the radar prefers N-1
hits over 0). The orchestrator (service.py) compares `body.nbHits` vs
returned length and logs if it cares.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .patterns import ARXIV_URL_RE, HF_PAPERS_URL_RE
from .schemas import Hit


def parse_search_response(body: Any) -> list[Hit]:
    """Parse Algolia HN /search or /search_by_date response into Hit objects."""
    if not isinstance(body, dict):
        return []
    hits: list[Hit] = []
    for raw in body.get("hits") or []:
        try:
            hit = _parse_hit(raw)
            if hit is not None:
                hits.append(hit)
        except (ValueError, KeyError, TypeError):
            continue
    return hits


def _parse_hit(raw: Any) -> Hit | None:
    """Pure: one Algolia hit → Hit. Returns None when objectID or title is
    missing (a story with neither isn't usable for radar surfacing)."""
    if not isinstance(raw, dict):
        return None

    hn_id = str(raw.get("objectID") or "").strip()
    if not hn_id:
        return None

    # Comments return `comment_text` + `story_title` instead of `title`.
    # Stories return `title`. Algolia returns one or the other.
    title = (raw.get("title") or raw.get("story_title") or "").strip()
    if not title:
        return None

    url = _optional_str(raw.get("url"))
    arxiv_id = extract_arxiv_id_from_url(url) if url else None

    return Hit(
        hn_id=hn_id,
        title=" ".join(title.split()),
        url=url,
        author=str(raw.get("author") or "").strip() or "(unknown)",
        points=_int_default(raw.get("points")),
        num_comments=_int_default(raw.get("num_comments")),
        created_at=_parse_created_at(raw.get("created_at_i"), raw.get("created_at")),
        hn_url=f"https://news.ycombinator.com/item?id={hn_id}",
        arxiv_id=arxiv_id,
        story_text=_optional_str(raw.get("story_text")),
        tags=[str(t) for t in (raw.get("_tags") or []) if t],
    )


def extract_arxiv_id_from_url(url: str) -> str | None:
    """The cross-source dedup key. Matches `arxiv.org/(abs|pdf)/<id>` or
    `huggingface.co/papers/<id>` (HF embeds the arxiv id directly).
    Returns the arxiv id without any version suffix collapse — preserve
    versioning so the agent can decide whether to merge v1 and v2."""
    if not url:
        return None
    m = ARXIV_URL_RE.search(url)
    if m:
        return m.group(1)
    m = HF_PAPERS_URL_RE.search(url)
    if m:
        return m.group(1)
    return None


def _optional_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int_default(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _parse_created_at(unix_ts: Any, iso_str: Any) -> datetime | None:
    """Prefer the Unix timestamp (`created_at_i`) — Algolia returns it as an
    int, no timezone ambiguity. Fall back to the ISO-8601 string."""
    if unix_ts is not None:
        try:
            return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass
    if isinstance(iso_str, str) and iso_str:
        try:
            return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None
