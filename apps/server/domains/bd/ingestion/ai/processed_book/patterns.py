"""Regular expressions used to normalize and classify processed-book headings."""
from __future__ import annotations

import re

MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}(#{1,8})\s+(.+?)\s*#*\s*$", re.MULTILINE)
WORD_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
MAIN_STRUCTURE_HEADING_RE = re.compile(
    r"^(?:introduction|prologue|chapter\s+[ivxlcdm0-9]+|part\s+[ivxlcdm0-9]+|appendix\s+[a-z0-9]+)\b",
)
NUMBER_OR_ROMAN_NUMERAL_RE = re.compile(r"(?:\d+|[ivxlcdm]+)")
PART_OR_CHAPTER_NUMERAL_RE = re.compile(r"(?:part|chapter)\s+[ivxlcdm0-9]+")
