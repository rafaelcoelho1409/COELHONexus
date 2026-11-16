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
WHITESPACE_CHARACTER_RE = re.compile(r"\s")
SPLIT_LINE_HYPHEN_RE = re.compile(r"(.+?)[-‐‑]\s+(.+)")
MARKDOWN_HEADING_PREFIX_RE = re.compile(r"[ \t]*#{1,6}[ \t]+")
DETACHED_HEADING_PLURAL_SUFFIX_RE = re.compile(r"([ \t]+)S[ \t]*")
PROTECTED_CYBER_TOKEN_RE = re.compile(
    r"https?://\S+|www\.\S+|\b(?:\d{1,3}\.){3}\d{1,3}\b|"
    r"\bCVE-\d{4}-\d+\b|\b[A-Fa-f0-9]{32,}\b|"
    r"\b[\w.-]+\.(?:exe|dll|sys|bat|cmd|ps1|vbs|js|jar|apk|msi|conf|config|bin)\b",
    re.IGNORECASE,
)


def count_whole_word_occurrences(text: str, word: str) -> int:
    expression = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)")
    return len(expression.findall(text))
