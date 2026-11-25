"""Regular expressions used by visual enrichment Markdown serialization."""
from __future__ import annotations

import re


LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_+#.-]{0,32}$")
BLOCKED_TEX_RE = re.compile(
    r"\\(?:html|href|url|includegraphics|input|include|def|gdef|write|openout|read|csname)\b",
    re.IGNORECASE,
)
IMAGE_COMMENT_RE = re.compile(r"<!--\s*image\s*-->", re.IGNORECASE)
BACKTICK_RUN_RE = re.compile(r"`+")
MARKDOWN_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]<>#+.!|~])")
