"""Pure parsing of HF /api/daily_papers responses — Functional Core.

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no clocks, no logging,
no mutable globals. Deterministic in / deterministic out. Trivially unit-
testable (no httpx mock, no event loop).

Robustness: malformed entries are silently dropped (the radar prefers N-1
papers over 0). Entries without an `arxiv_id` are skipped — without it,
cross-source dedup with the arxiv tool can't work, and the paper has no
canonical URL.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .schemas import Paper


def parse_daily_papers_response(body: Any) -> list[Paper]:
    """Parse the JSON response (a list of daily-paper entries) into Paper
    objects. The endpoint returns a top-level JSON ARRAY (not a dict)."""
    if not isinstance(body, list):
        return []
    papers: list[Paper] = []
    for raw in body:
        try:
            paper = _parse_entry(raw)
            if paper is not None:
                papers.append(paper)
        except (ValueError, KeyError, TypeError):
            continue
    return papers


def _parse_entry(raw: Any) -> Paper | None:
    """Pure: one daily-paper entry → Paper. Returns None when the entry
    lacks the required `arxiv_id` (the cross-source link) or `title`."""
    if not isinstance(raw, dict):
        return None

    # HF entries nest most signal fields under `paper` (the arxiv record) and
    # carry submission-side fields (numComments, thumbnail) at the top level.
    paper_obj = raw.get("paper") if isinstance(raw.get("paper"), dict) else {}

    arxiv_id = str(paper_obj.get("id") or "").strip()
    if not arxiv_id:
        return None

    title = (paper_obj.get("title") or raw.get("title") or "").strip()
    if not title:
        return None

    return Paper(
        arxiv_id=arxiv_id,
        title=" ".join(title.split()),
        abstract=_optional_str(paper_obj.get("summary") or raw.get("summary")),
        authors=[
            str(a.get("name", "")).strip()
            for a in (paper_obj.get("authors") or [])
            if isinstance(a, dict) and a.get("name")
        ],
        published=_parse_pub_date(paper_obj.get("publishedAt")),
        upvotes=_int_default(paper_obj.get("upvotes")),
        num_comments=_int_default(raw.get("numComments")),
        discussion_id=_optional_str(paper_obj.get("discussionId")),
        thumbnail=_optional_str(raw.get("thumbnail")),
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        hf_url=f"https://huggingface.co/papers/{arxiv_id}",
    )


def _optional_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int_default(v: Any, default: int = 0) -> int:
    """Coerce to int with a default — HF occasionally emits null for
    upvotes/numComments on freshly-submitted entries."""
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _parse_pub_date(s: Any) -> date | None:
    """HF emits ISO 8601 timestamps like `2026-06-10T17:00:00.000Z`.
    Return the date component only; the timestamp's time-of-day is irrelevant
    for radar use and creates timezone-comparison headaches downstream."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None
