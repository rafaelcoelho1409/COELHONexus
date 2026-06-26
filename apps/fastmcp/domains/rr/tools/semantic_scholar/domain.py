"""Pure parsing of Semantic Scholar /paper/search responses — Functional Core.

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no clocks, no logging,
no mutable globals. Deterministic in / deterministic out. Trivially unit-
testable (no httpx mock, no event loop).

Robustness: malformed entries are silently dropped (the radar prefers N-1
papers over 0). The orchestrator (service.py) compares response['data']
length vs returned list length and logs the discrepancy if it cares.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .schemas import Paper


def parse_search_response(body: dict[str, Any]) -> list[Paper]:
    """Parse an S2 /paper/search response into Paper objects."""
    papers: list[Paper] = []
    for raw in body.get("data") or []:
        try:
            papers.append(_parse_paper(raw))
        except (ValueError, KeyError, TypeError):
            continue
    return papers


def _parse_paper(raw: dict[str, Any]) -> Paper:
    """Pure: one entry from /paper/search → Paper."""
    paper_id = raw.get("paperId")
    title = raw.get("title")
    if not paper_id or not title:
        raise ValueError("missing required field: paperId or title")

    return Paper(
        s2_id=str(paper_id),
        title=" ".join(str(title).split()),
        abstract=_optional_str(raw.get("abstract")),
        tldr=_parse_tldr(raw.get("tldr")),
        authors=[
            str(a.get("name", "")).strip()
            for a in (raw.get("authors") or [])
            if isinstance(a, dict) and a.get("name")
        ],
        year=_optional_int(raw.get("year")),
        publication_date=_parse_pub_date(raw.get("publicationDate")),
        citation_count=_int_default(raw.get("citationCount")),
        influential_citation_count=_int_default(raw.get("influentialCitationCount")),
        reference_count=_int_default(raw.get("referenceCount")),
        venue=_optional_str(raw.get("venue")),
        fields_of_study=[
            str(f) for f in (raw.get("fieldsOfStudy") or []) if f
        ],
        open_access_pdf=_parse_oa_pdf(raw.get("openAccessPdf")),
        external_ids=_normalize_external_ids(raw.get("externalIds") or {}),
    )


def _optional_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _optional_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _int_default(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _parse_pub_date(s: Any) -> date | None:
    """S2's publicationDate can be 'YYYY-MM-DD', 'YYYY-MM', 'YYYY', or null.
    Only YYYY-MM-DD parses to a date — partial dates yield None (the `year`
    field still captures the year)."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _parse_tldr(v: Any) -> str | None:
    """S2 emits `{"model": "...", "text": "..."}` or null."""
    if isinstance(v, dict):
        text = v.get("text")
        if text:
            cleaned = " ".join(str(text).split())
            return cleaned or None
    return None


def _parse_oa_pdf(v: Any) -> str | None:
    """S2 emits `{"url": "...", "status": "..."}` or null."""
    if isinstance(v, dict):
        url = v.get("url")
        if url:
            return str(url).strip() or None
    return None


def _normalize_external_ids(raw: dict[str, Any]) -> dict[str, str]:
    """S2 emits `{DOI: '10.x', ArXiv: '2406.x', PubMed: '12345', MAG: '...'}`.
    Coerce all values to str for consistent downstream handling — some ID
    types (MAG, CorpusId) come back as ints."""
    return {
        str(k): str(v)
        for k, v in raw.items()
        if v is not None and str(v).strip()
    }
