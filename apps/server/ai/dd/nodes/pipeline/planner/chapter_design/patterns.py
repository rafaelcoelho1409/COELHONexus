"""Compiled lexical patterns used by Planner v2 chapter design."""
from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{1,}", re.IGNORECASE)
CONCEPT_RE = re.compile(r"[^a-z0-9]+")
REFERENCE_SUFFIX_RE = re.compile(r"(?:\s+(?:api\s+)?reference)+$", re.IGNORECASE)

STOP_WORDS = frozenset({
    "the", "and", "for", "this", "that", "with", "from", "into",
    "your", "you", "are", "was", "use", "using", "how", "can",
    "will", "not", "but", "its", "has", "have", "guide", "docs",
    "page", "overview", "reference", "documentation", "chapter",
    "learn", "learns",
})
