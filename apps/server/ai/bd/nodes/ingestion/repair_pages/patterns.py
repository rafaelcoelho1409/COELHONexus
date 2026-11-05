"""Regular expressions used to recognize safe Markdown table normalizations."""
import re


_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_PAGE_REFERENCE_RE = re.compile(r"(?:\d{1,4}|[ivxlcdm]{1,8})$", re.IGNORECASE)
_DOT_LEADER = r"(?:[.·]\s*){2,}|…+"
_LEADING_DOT_LEADER_PAGE_REFERENCE_RE = re.compile(
    rf"^\s*(?:{_DOT_LEADER})\s*(?P<page>\d{{1,4}}|[ivxlcdm]{{1,8}})\s*$",
    re.IGNORECASE,
)
_INLINE_TOC_ROW_RE = re.compile(
    rf"^(?P<title>.+?\S)\s*(?:{_DOT_LEADER})\s*(?P<page>\d{{1,4}}|[ivxlcdm]{{1,8}})$",
    re.IGNORECASE,
)
_TRAILING_LEADERS_RE = re.compile(rf"\s*(?:{_DOT_LEADER})\s*$")
