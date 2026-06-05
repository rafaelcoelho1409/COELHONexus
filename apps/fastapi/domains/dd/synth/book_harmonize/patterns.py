"""book_harmonize pre-compiled regex (JSON extraction)."""
from __future__ import annotations

import re


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
