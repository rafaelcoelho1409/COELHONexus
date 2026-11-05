from __future__ import annotations
import re


MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
IMAGE_PLACEHOLDER_RE = re.compile(r"<!--\s*(?:image|picture)\s*-->", re.IGNORECASE)
SPACE_RUN_RE = re.compile(r"[ \t]{2,}")
CAPTION_LINE_RE = re.compile(r"^\s*(?:Figure|Fig\.|Table|Listing|Exhibit)\s+[\w.-]+\s*[:.]", re.IGNORECASE)
PAGE_NUMBER_ONLY_RE =re.compile(r"^\s*(?:[-–—]\s*)?\d{1,4}(?:\s*[-–—])?\s*$")
TOC_DOTTED_LEADER_RE = re.compile(r"(?m)^.{2,}?(?:\.{3,}|…{1,}|·{3,})\s*\d{1,4}\s*$")
DOT_LEADER_MARKER_RE = re.compile(r"(?:[.·]\s*){2,}|…+")
HEADING_RE = re.compile(r"(?m)^\s*(?:#{1,6}\s+)?([A-Z][A-Z0-9\s'’:&—–-]{4,120})\s*$")
HEADING_FRAGMENT_RE = re.compile(r"(?<![A-Z0-9])[A-Z]\s+[A-Z]{5,}(?![A-Z0-9])")
STANDALONE_LETTER_RE = re.compile(r"(?<![A-Z0-9'’])[A-Z](?![A-Z0-9'’])")
TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
