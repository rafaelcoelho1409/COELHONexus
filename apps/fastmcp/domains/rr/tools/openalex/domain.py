"""Pure parsing of OpenAlex /works responses — Functional Core.

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no clocks, no logging,
no mutable globals. Deterministic in / deterministic out. Trivially unit-
testable (no httpx mock, no event loop).

Robustness: malformed entries are silently dropped (the radar prefers N-1
papers over 0), same policy as semantic_scholar/domain.py.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .schemas import Paper


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stores abstracts as an inverted index (word -> positions)
    instead of plain text — a licensing/copyright workaround, not a data
    error. Rebuild the plain text by placing each word at every position
    it occupies, then joining left-to-right. Returns None for a missing
    or empty index (most paywalled/older works have none)."""
    if not inverted_index:
        return None
    max_pos = -1
    for positions in inverted_index.values():
        for p in positions:
            if p > max_pos:
                max_pos = p
    if max_pos < 0:
        return None
    slots: list[str] = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for p in positions:
            if 0 <= p <= max_pos:
                slots[p] = word
    text = " ".join(s for s in slots if s)
    return text or None


def parse_search_response(body: dict[str, Any]) -> list[Paper]:
    """Parse an OpenAlex /works response into Paper objects."""
    papers: list[Paper] = []
    for raw in body.get("results") or []:
        try:
            papers.append(_parse_paper(raw))
        except (ValueError, KeyError, TypeError):
            continue
    return papers


def _parse_paper(raw: dict[str, Any]) -> Paper:
    """Pure: one entry from /works results → Paper."""
    openalex_id = _short_id(raw.get("id"))
    title = raw.get("title") or raw.get("display_name")
    if not openalex_id or not title:
        raise ValueError("missing required field: id or title")

    return Paper(
        openalex_id=openalex_id,
        title=" ".join(str(title).split()),
        abstract=reconstruct_abstract(raw.get("abstract_inverted_index")),
        authors=_parse_authors(raw.get("authorships")),
        publication_year=_optional_int(raw.get("publication_year")),
        publication_date=_parse_date(raw.get("publication_date")),
        cited_by_count=_int_default(raw.get("cited_by_count")),
        work_type=_optional_str(raw.get("type")),
        is_open_access=_parse_is_oa(raw.get("open_access")),
        open_access_pdf=_parse_oa_url(raw.get("open_access")),
        topics=_parse_topics(raw),
        external_ids=_normalize_external_ids(raw.get("ids") or {}),
    )


def _short_id(full_id: Any) -> str | None:
    """OpenAlex IDs come back as full URLs (`https://openalex.org/W123`);
    the short form (`W123`) is what the rest of the pipeline expects."""
    if not full_id:
        return None
    s = str(full_id).strip()
    return s.rsplit("/", 1)[-1] or None


def _parse_authors(authorships: Any) -> list[str]:
    if not isinstance(authorships, list):
        return []
    names: list[str] = []
    for a in authorships:
        if not isinstance(a, dict):
            continue
        author = a.get("author") or {}
        name = author.get("display_name") if isinstance(author, dict) else None
        if name:
            names.append(str(name).strip())
    return names


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


def _parse_date(s: Any) -> date | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _parse_is_oa(oa: Any) -> bool:
    return bool(isinstance(oa, dict) and oa.get("is_oa"))


def _parse_oa_url(oa: Any) -> str | None:
    if isinstance(oa, dict):
        url = oa.get("oa_url")
        if url:
            return str(url).strip() or None
    return None


def _parse_topics(raw: dict[str, Any]) -> list[str]:
    """OpenAlex's schema migrated `concepts` -> `topics` — accept either
    shape defensively since both have shipped across API versions.
    Capped at 5, matching the field's typical cardinality."""
    names: list[str] = []
    for key in ("topics", "concepts"):
        items = raw.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("display_name"):
                names.append(str(item["display_name"]).strip())
        if names:
            break
    return names[:5]


def _normalize_external_ids(raw: dict[str, Any]) -> dict[str, str]:
    """OpenAlex's `ids` object carries full URLs for doi/pmid/pmcid
    (`https://doi.org/10.x`, `https://pubmed.ncbi.nlm.nih.gov/123`) —
    strip to the bare identifier so downstream cross-source dedup matches
    the bare-ID convention the other tools already use. `openalex` and
    `mag` are excluded: openalex_id is already the Paper's own id field,
    and mag is a discontinued Microsoft Academic Graph ID with no
    cross-source use here."""
    out: dict[str, str] = {}
    doi = raw.get("doi")
    if doi:
        out["DOI"] = str(doi).rsplit("doi.org/", 1)[-1]
    pmid = raw.get("pmid")
    if pmid:
        out["PubMed"] = str(pmid).rsplit("/", 1)[-1]
    pmcid = raw.get("pmcid")
    if pmcid:
        out["PMC"] = str(pmcid).rsplit("/", 1)[-1]
    return out
