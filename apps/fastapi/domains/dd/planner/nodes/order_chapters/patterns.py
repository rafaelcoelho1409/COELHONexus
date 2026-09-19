"""order_chapters — pre-compiled regex (foundational keyword detector +
JSON envelope extractor)."""
from __future__ import annotations
from . import params

import re



FOUNDATIONAL_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in params.FOUNDATIONAL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
JSON_RE = re.compile(r"\{.*?\}|\[.*?\]", re.DOTALL)
