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

# 2026-06-05 — RESCUE FLOOR for the post-scoring rescue pass.
#
# Memory `project_planner_cc_coverage_2026_05_29` flagged ~18 GENUINE
# content docs silently dropped at chapter_select because their LLM
# confidence scores were JUST below CONFIDENCE_THRESHOLD across every
# proposal. The judge was effectively saying "this fits multiple
# chapters at 0.4, none cleanly" — but greedy_select's `assignable`
# filter excludes everything below 0.5, so those docs never reach a
# chapter.
#
# RESCUE policy: if a doc's MAX confidence across all proposals is
# below CONFIDENCE_THRESHOLD but >= RESCUE_FLOOR, floor its best score
# up to CONFIDENCE_THRESHOLD. This preserves single-membership semantics
# (only ONE score is rescued — the best one) and keeps the lineage of
# what the LLM actually scored visible in the persisted blob via a
# `rescued` list. Docs whose max is below RESCUE_FLOOR are left alone —
# the LLM is genuinely saying "this doesn't belong anywhere", and
# forcing them in would inject noise into chapter content.
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
