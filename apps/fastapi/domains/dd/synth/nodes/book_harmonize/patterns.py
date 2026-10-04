"""book_harmonize pre-compiled regex (JSON extraction, fenced code blocks)."""
from __future__ import annotations

import re


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
# A complete fenced code block (``` or ~~~, any info string), non-greedy up to
# the matching closer of the same marker.
FENCED_BLOCK_RE = re.compile(r"(?ms)^[ \t]*(```|~~~)[^\n]*\n.*?^[ \t]*\1[ \t]*$")
