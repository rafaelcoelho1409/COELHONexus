"""Regular expressions used by metadata normalization."""
from __future__ import annotations

import re


DOCUMENT_SUFFIX_RE = re.compile(r"\.(?:pdf|epub)$", re.IGNORECASE)
SOURCE_SITE_TAG_RE = re.compile(
    r"\s*[(\[][^()\[\]]*(?:z-?lib(?:rary)?|1lib|libgen|pdfdrive|anna['’]?s[\s-]?archive|b-ok|bookzz)[^()\[\]]*[)\]]",
    re.IGNORECASE,
)
TITLE_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,2}\s+([^\n]{3,180})\s*#*\s*$")
WHITESPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^\w]+")
WORD_RE = re.compile(r"[\w]+")
