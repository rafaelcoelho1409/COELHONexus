"""doc_distill — pre-compiled regex (JSON extraction + fallback identifiers)."""
from __future__ import annotations

import re


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
FB_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
H1_RE = re.compile(r"(?m)^#\s+(.+)$")
