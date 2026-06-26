"""Pure parsing of arXiv Atom-feed responses — the Functional Core.

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no network, no logging,
no clocks, no mutable globals. Deterministic in / deterministic out.
Trivially unit-testable: feed XML string in, get list[Paper] out.
"""
from datetime import datetime
from xml.etree import ElementTree as ET

from .keys import ATOM_NAMESPACES
from .schemas import Paper, SearchInput


def build_search_query(req: SearchInput) -> str:
    """Compose arXiv's `search_query` string from a SearchInput.

    Wraps the free-text query in DOUBLE QUOTES for **phrase matching**. Without
    quotes, arXiv tokenizes the query (`all:deep agents` → `all:deep AND agents`)
    and unprefixed tokens fall into a less-strict default scope that bypasses
    the `cat:` filter — empirically observed 2026-06-10 (a cs.CV paper slipped
    through a `cat:cs.LG OR cat:cs.AI` filter). Phrase matching restores the
    expected behavior. Internal `"` characters are dropped (rare; not worth
    escaping per arXiv API guidance).

    Categories are AND-combined with the phrase; multiple categories OR among
    themselves.
    """
    phrase = req.query.replace('"', "").strip()
    parts = [f'all:"{phrase}"']
    if req.categories:
        cat_expr = " OR ".join(f"cat:{c}" for c in req.categories)
        parts.append(f"({cat_expr})")
    return " AND ".join(parts)


def parse_atom_feed(xml_str: str) -> list[Paper]:
    """Parse an arXiv API Atom XML response into a list of Paper objects.

    Returns an empty list if the feed has no <entry> elements (zero hits is
    a valid result). Raises ValueError on malformed XML so the tool layer
    can convert it to a ToolError.
    """
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        raise ValueError(f"Malformed arXiv Atom feed: {e}") from e

    return [_parse_entry(entry) for entry in root.findall("atom:entry", ATOM_NAMESPACES)]


def _parse_entry(entry: ET.Element) -> Paper:
    """Pure: <atom:entry> element → Paper."""
    arxiv_id = _strip_id(_text(entry, "atom:id"))
    title = _collapse_ws(_text(entry, "atom:title"))
    abstract = _collapse_ws(_text(entry, "atom:summary"))

    authors = [
        _text(a, "atom:name")
        for a in entry.findall("atom:author", ATOM_NAMESPACES)
    ]

    primary_cat_elem = entry.find("arxiv:primary_category", ATOM_NAMESPACES)
    primary_category = (
        primary_cat_elem.get("term", "") if primary_cat_elem is not None else ""
    )

    categories = [
        c.get("term", "")
        for c in entry.findall("atom:category", ATOM_NAMESPACES)
        if c.get("term")
    ]

    published = _isoparse(_text(entry, "atom:published"))
    updated = _isoparse(_text(entry, "atom:updated"))

    # arXiv emits two <link> elements per entry: one with rel='alternate'
    # (the /abs/ HTML page) and one with title='pdf' (the PDF). Pick by attr.
    pdf_url = ""
    abs_url = ""
    for link in entry.findall("atom:link", ATOM_NAMESPACES):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "")
        elif link.get("rel") == "alternate":
            abs_url = link.get("href", "")

    doi = _optional_text(entry, "arxiv:doi")
    comment = _optional_text(entry, "arxiv:comment")

    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        abstract=abstract,
        authors=authors,
        primary_category=primary_category,
        categories=categories,
        published=published,
        updated=updated,
        pdf_url=pdf_url,
        abs_url=abs_url or f"https://arxiv.org/abs/{arxiv_id}",
        doi=doi,
        comment=comment,
    )


def _text(elem: ET.Element, path: str) -> str:
    """Get the text under `path` (relative to elem), stripped. Empty if missing."""
    found = elem.find(path, ATOM_NAMESPACES)
    return (found.text or "").strip() if found is not None else ""


def _optional_text(elem: ET.Element, path: str) -> str | None:
    """Like _text but returns None instead of empty string when absent."""
    found = elem.find(path, ATOM_NAMESPACES)
    if found is None or not found.text:
        return None
    return found.text.strip()


def _collapse_ws(s: str) -> str:
    """Collapse internal whitespace runs — arXiv pretty-prints title/summary."""
    return " ".join(s.split())


def _strip_id(atom_id: str) -> str:
    """'http://arxiv.org/abs/2406.12345v2' → '2406.12345v2'."""
    return atom_id.rsplit("/", 1)[-1] if atom_id else ""


def _isoparse(s: str) -> datetime:
    """RFC 3339 → tz-aware datetime. arXiv emits the 'Z' UTC suffix; Python
    3.11+ datetime.fromisoformat handles it, but we normalize defensively."""
    if not s:
        return datetime.min
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
