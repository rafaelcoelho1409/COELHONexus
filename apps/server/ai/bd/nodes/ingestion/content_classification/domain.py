"""Pure policy for turning a page-role assessment into a conservative selection."""
from __future__ import annotations

import re

import domains


_CONTENTS_TITLE = re.compile(
    r"^(?:(?:brief|detailed)\s+)?(?:table\s+of\s+)?contents"
    r"(?:\s+(?:in\s+detail|continued|\(continued\)))?\s*[:.]?$",
    re.I,
)
_CONTENTS_LEADER = re.compile(
    r"^\s*\S.{1,180}?(?:\.{2,}|…{2,}|·{2,}|_{2,}|\t+)\s*(?:\d{1,4}|[ivxlcdm]{1,8})\s*$",
    re.I,
)
_CONTENTS_NUMBERED_ENTRY = re.compile(
    r"^\s*(?:(?:chapter|part|appendix)\s+)?(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.)]?\s+\S.{2,140}?"
    r"\s{2,}(?:\d{1,4}|[ivxlcdm]{1,8})\s*$",
    re.I,
)
_CONTENTS_TRAILING_PAGE = re.compile(r"^\s*\S.{2,160}?\s{2,}(?:\d{1,4}|[ivxlcdm]{1,8})\s*$", re.I)
_CONTENTS_TABLE_PAGE = re.compile(r"^[.\s·…]*(?:\d{1,4}|[ivxlcdm]{1,8})[.\s·…]*$", re.I)


def is_source_contents_page(markdown: str, section_path: list[str] | None = None) -> bool:
    """Identify printed contents pages so they never enter the processed study-page set."""
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return False

    normalized_lines = [re.sub(r"^\s{0,3}#{1,6}\s*|[*_`]", "", line).strip() for line in lines]
    has_contents_title = any(_CONTENTS_TITLE.fullmatch(line) for line in normalized_lines[:8])
    has_contents_section = any(
        isinstance(value, str)
        and _CONTENTS_TITLE.fullmatch(re.sub(r"^\s{0,3}#{1,6}\s*|[*_`]", "", value).strip())
        for value in (section_path or [])
    )
    leader_entries = sum(_CONTENTS_LEADER.fullmatch(line) is not None for line in lines)
    numbered_entries = sum(_CONTENTS_NUMBERED_ENTRY.fullmatch(line) is not None for line in lines)
    trailing_page_entries = sum(_CONTENTS_TRAILING_PAGE.fullmatch(line) is not None for line in lines)
    table_page_entries = sum(
        len(cells := [cell.strip() for cell in line.strip("|").split("|")]) >= 2
        and bool(cells[0])
        and _CONTENTS_TABLE_PAGE.fullmatch(cells[-1]) is not None
        for line in lines
        if line.startswith("|") and line.endswith("|")
    )

    if has_contents_title:
        return (
            leader_entries >= 1
            or numbered_entries >= 2
            or trailing_page_entries >= 2
            or table_page_entries >= 2
            or len(lines) >= 8
        )
    if has_contents_section and table_page_entries >= 2:
        return True
    return leader_entries >= 4 or numbered_entries >= 6 or trailing_page_entries >= 6


def excerpt(markdown: str) -> str:
    """Keep the beginning and end of a long page for the bounded classifier prompt."""
    limit = domains.bd.ingestion.ai.params.CONTENT_CLASSIFICATION_EXCERPT_CHARS
    if len(markdown) <= limit:
        return markdown
    first = int(limit * 0.75)
    return f"{markdown[:first]}\n\n[Middle omitted for bounded page classification]\n\n{markdown[-(limit - first):]}"


def disposition(role: str, confidence: float, *, delivery_disposition: str, has_pictures: bool) -> tuple[str, str]:
    """Exclude only known non-learning matter with high confidence; retain ambiguous and image-only pages."""
    if delivery_disposition == "discarded_blank":
        return "exclude", "content_policy"
    if has_pictures and role == "unknown":
        return "include", "conservative_fallback"
    if (
        role in {"navigation", "legal", "noise"}
        and confidence >= domains.bd.ingestion.ai.params.CONTENT_EXCLUSION_CONFIDENCE
    ):
        return "exclude", "content_policy"
    return "include", "classifier"
