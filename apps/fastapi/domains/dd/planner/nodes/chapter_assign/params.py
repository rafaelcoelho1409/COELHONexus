"""chapter_assign tunables — concurrency + LLM caps + confidence cutoff."""
from __future__ import annotations


# 16-way concurrency produced 14% rate-limit failures on NIM+Mistral
CONCURRENCY = 8

MAX_TOKENS = 600
TEMPERATURE = 0.0
MAX_REPAIR_ATTEMPTS = 1

BODY_CHARS = 4_000

CONFIDENCE_THRESHOLD = 0.5

# docs with max confidence in [RESCUE_FLOOR, CONFIDENCE_THRESHOLD) get floored up so they're not silently dropped
RESCUE_FLOOR = 0.3

BLOB_PREFIX = "planner"

# Stop-words for the lexical-overlap fallback assignment.
FB_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into",
    "via", "are", "use", "how", "you", "can", "will", "not", "but", "its",
    "has", "langfuse", "data", "api", "sdk", "using", "configure", "setup",
    "guide", "overview", "reference", "documentation", "covering", "usage",
    "concepts",
})
