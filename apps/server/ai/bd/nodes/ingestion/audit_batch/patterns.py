"""Regular expressions and text-pattern helpers used by page-audit validation."""
from __future__ import annotations

import re


MARKDOWN_TABLE_LINE_RE = re.compile(r"(?m)^\s*\|[^\n]*\|\s*$")
SPLIT_WORD_LINE_BREAK_RE = re.compile(r"\b[A-Za-z]{2,}[-‐‑][ \t]*\n[ \t]*[A-Za-z]{2,}\b")
MISSING_OF_HYPHEN_RE = re.compile(r"\b[A-Za-z]{3,}-of[A-Za-z]{4,}\b", re.IGNORECASE)
QUOTED_TERM_RE = re.compile(r"['\"“`]([A-Za-z][A-Za-z'’\-]{3,})['\"”`]")
VALID_SPLIT_WORD_TERM_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?(?:-[A-Za-z]+)*")
WHITESPACE_RE = re.compile(r"\s")
WHITESPACE_RUN_RE = re.compile(r"\s+")
TECHNICAL_CONTEXT_RE = re.compile(
    r"\b(?:encrypted|ciphertext|encoded|obfuscated|code sample|sample code|source code|payload|hash|checksum)\b",
    re.IGNORECASE,
)


def has_spaced_word_form(text: str, term: str) -> bool:
    """Return whether the excerpt contains a quoted term split by inserted whitespace."""
    if not term.isalpha() and not VALID_SPLIT_WORD_TERM_RE.fullmatch(term):
        return False
    spaced_term = r"\b" + r"\s*".join(re.escape(character) for character in term) + r"\b"
    match = re.search(spaced_term, text, re.IGNORECASE)
    return match is not None and WHITESPACE_RE.search(match.group()) is not None
