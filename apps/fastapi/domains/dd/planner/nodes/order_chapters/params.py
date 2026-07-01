"""order_chapters tunables — sampling + foundational keyword set."""
from __future__ import annotations


BLOB_PREFIX = "planner"

# Number of independent LLM ordering samples to draw before Borda
# aggregation. 3 is the USC sweet spot: enough diversity to reveal
# disagreement, cheap enough on free tiers (3 calls/study at ~10s each).
N_SAMPLES = 3
# Temperature for sampling. Slightly diversified to capture different
# valid orderings.
TEMPERATURE = 0.3
# Per-sample token budget — N chapter titles + N descriptions + ranking
# response. ~800 tokens fits up to ~16 chapters comfortably.
MAX_TOKENS = 800
# How many characters of each chapter description to include in the
# prompt. Two sentences is enough context for ordering; longer wastes
# tokens.
DESCRIPTION_CHARS = 240
# Concurrency for the N parallel sample calls.
SAMPLE_CONCURRENCY = 3

# Foundational-prefix rule: these patterns pin the chapter to position 0 regardless of LLM ordering; only the FIRST match anchors.
FOUNDATIONAL_KEYWORDS = (
    "install",
    "installation",
    "setup",
    "getting started",
    "quickstart",
    "cli",
    "command line",
    "first steps",
)
