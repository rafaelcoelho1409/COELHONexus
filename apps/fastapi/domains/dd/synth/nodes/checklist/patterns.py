"""checklist patterns — pre-compiled parser regexes (no behavior)."""
from __future__ import annotations

import re


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Keyword-overlap pre-check: zero shared identifiers between prose and code → misaligned (no LLM call needed).
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


ATOMIC_CLAIM_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
