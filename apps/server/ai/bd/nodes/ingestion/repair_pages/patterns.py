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
_SPACE_RE = re.compile(r"[ \t­]+")
_INTRA_WORD_HYPHEN_RE = re.compile(r"(?<=\w)-(?=\w)")
_DANGLING_HYPHEN_RE = re.compile(r"(?<=\w)-[ \t]+(?=\w)")
_LINK_DESTINATION_RE = re.compile(r"\]\(([^)]*)\)")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PLAIN_PROSE_RE = re.compile(r"[A-Za-z' ­\-]+")
_MARKDOWN_MARKER_RE = re.compile(r"(?m)^[ \t]*(?:[-*+>]|\d+[.)]|#{1,6})[ \t]+")
