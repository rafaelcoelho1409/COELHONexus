"""Regular expressions used by BD Ask answer formatting."""
from __future__ import annotations

import re


INLINE_CITATION_RE = re.compile(
    r"\[(?:Page:\s*\d+|Source:\s*[^\]]+)\]",
    re.IGNORECASE,
)
