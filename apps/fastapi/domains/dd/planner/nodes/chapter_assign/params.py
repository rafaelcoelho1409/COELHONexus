"""chapter_assign tunables — concurrency + LLM caps + confidence cutoff."""
from __future__ import annotations


# SOTA Sept 2026: 24×138 burst → 103 lexical fallback (75%) on free-tier 402/timeout;
# 24→12 cuts burst 50% and pooled 200/100 still saturates, 600tok needs 45s not 30s.
CONCURRENCY = 12

MAX_TOKENS = 1200   # was 600 — scores needs one entry per chapter proposal,
# and PROPOSALS_MAX=30 alone eats ~450-600 tokens of pure JSON in the worst
# case, leaving zero headroom for a reasoning model's <think> preamble (same
# empty-response failure mode confirmed in off_topic/doc_distill).
TEMPERATURE = 0.0
TIMEOUT_S = 60.0    # was 45s — paired with the larger token budget above
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
