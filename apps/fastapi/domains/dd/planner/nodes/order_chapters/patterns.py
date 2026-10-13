"""order_chapters — pre-compiled regex (foundational keyword detector)."""
from __future__ import annotations
from . import params

import re



FOUNDATIONAL_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in params.FOUNDATIONAL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Chapters for readers who already know an older version (migration / upgrade guides, changelogs):
# they close the book, ahead of the generic appendix.
TRAILING_RE = re.compile(
    r"\b(migrat\w*|upgrad\w*|changelog|release notes|deprecat\w*|breaking changes)\b",
    re.IGNORECASE,
)
