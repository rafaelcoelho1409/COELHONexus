"""chapter_assign tunables — concurrency + LLM caps + confidence cutoff."""
from __future__ import annotations


# Concurrency for per-doc scoring.
# 2026-05-27 P1 — lowered 16 → 8 to match doc_distill after Claude Code
# Run produced 14% rate-limit failures with 16-way concurrency. Same
# diagnosis (NIM+Mistral saturation), same fix.
CONCURRENCY = 8

# LLM call settings (per-doc scoring is short).
MAX_TOKENS = 600
TEMPERATURE = 0.0
MAX_REPAIR_ATTEMPTS = 1

# Per-doc body cap when no distillate is available.
BODY_CHARS = 4_000

# Confidence threshold for considering a doc "assigned" to a chapter.
# chapter_select uses this to gate which assignments count for coverage.
CONFIDENCE_THRESHOLD = 0.5

BLOB_PREFIX = "planner"

# Stop-words for the lexical-overlap fallback assignment.
FB_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into",
    "via", "are", "use", "how", "you", "can", "will", "not", "but", "its",
    "has", "langfuse", "data", "api", "sdk", "using", "configure", "setup",
    "guide", "overview", "reference", "documentation", "covering", "usage",
    "concepts",
})
