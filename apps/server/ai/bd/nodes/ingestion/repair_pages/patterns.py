"""Regular expressions used by page repair validation and normalization."""
from __future__ import annotations

import re


TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
PAGE_REFERENCE_RE = re.compile(r"(?:\d{1,4}|[ivxlcdm]{1,8})$", re.IGNORECASE)
_DOT_LEADER = r"(?:[.·]\s*){2,}|…+"
LEADING_DOT_LEADER_PAGE_REFERENCE_RE = re.compile(
    rf"^\s*(?:{_DOT_LEADER})\s*(?P<page>\d{{1,4}}|[ivxlcdm]{{1,8}})\s*$",
    re.IGNORECASE,
)
INLINE_TOC_ROW_RE = re.compile(
    rf"^(?P<title>.+?\S)\s*(?:{_DOT_LEADER})\s*(?P<page>\d{{1,4}}|[ivxlcdm]{{1,8}})$",
    re.IGNORECASE,
)
TRAILING_LEADERS_RE = re.compile(rf"\s*(?:{_DOT_LEADER})\s*$")
SPACE_RE = re.compile(r"[ \t­]+")
# A soft hyphen the extraction left before a space and a lowercase word is a hyphenated line break: the word continues.
SOFT_HYPHEN_BREAK_RE = re.compile(r"(?<=[A-Za-z])­[ \t]+(?=[a-z])")
INTRA_WORD_HYPHEN_RE = re.compile(r"(?<=\w)-(?=\w)")
DANGLING_HYPHEN_RE = re.compile(r"(?<=\w)-[ \t]+(?=\w)")
LINK_DESTINATION_RE = re.compile(r"\]\(([^)]*)\)")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
PLAIN_PROSE_RE = re.compile(r"[A-Za-z' ­\-]+")
MARKDOWN_MARKER_RE = re.compile(r"(?m)^[ \t]*(?:[-*+>]|\d+[.)]|#{1,6})[ \t]+")
WHITESPACE_RE = re.compile(r"\s+")


def count_whole_word_occurrences(text: str, word: str) -> int:
    expression = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)")
    return len(expression.findall(text))
