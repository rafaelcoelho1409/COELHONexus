"""order_chapters — pre-compiled regex (foundational keyword detector +
JSON envelope extractor)."""
from __future__ import annotations

import re

from .params import FOUNDATIONAL_KEYWORDS


FOUNDATIONAL_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in FOUNDATIONAL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
JSON_RE = re.compile(r"\{.*?\}|\[.*?\]", re.DOTALL)
