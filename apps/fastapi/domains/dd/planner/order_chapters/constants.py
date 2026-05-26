"""order_chapters constants — tunables + version strings."""
from __future__ import annotations

import re


_BLOB_PREFIX = "planner"
# Bumped when prompt or scoring policy changes. Cache is invalidated cleanly.
_PROMPT_VERSION = "v1-2026-05-25"

# Number of independent LLM ordering samples to draw before Borda aggregation.
# 3 is the USC sweet spot: enough diversity to reveal disagreement, cheap
# enough on free tiers (3 calls/study at ~10s each).
_N_SAMPLES = 3
# Temperature for sampling. Slightly diversified to capture different valid
# orderings (the LLM's preferred ordering varies by which framework idiom it
# prioritizes — installation-first vs concept-first vs API-first).
_TEMPERATURE = 0.3
# Per-sample token budget — N chapter titles + N descriptions + ranking
# response. ~800 tokens fits up to ~16 chapters comfortably.
_MAX_TOKENS = 800
# How many characters of each chapter description to include in the prompt.
# Two sentences is enough context for ordering; longer wastes tokens.
_DESCRIPTION_CHARS = 240
# Concurrency for the N parallel sample calls. 3 in-flight on the dd-all
# rotator is well under any single-provider rate ceiling.
_SAMPLE_CONCURRENCY = 3

# Foundational-prefix rule: chapter titles matching any of these patterns
# get pinned to position 0 (installation/quickstart-style chapters MUST come
# first regardless of LLM ordering). Case-insensitive substring match.
# Multi-keyword: the FIRST matching chapter goes to position 0; subsequent
# matches keep their LLM-determined relative order after that anchor.
_FOUNDATIONAL_KEYWORDS = (
    "install",
    "installation",
    "setup",
    "getting started",
    "quickstart",
    "cli",
    "command line",
    "first steps",
)
# Compile once so the per-chapter check is O(1) regex eval.
_FOUNDATIONAL_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _FOUNDATIONAL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_JSON_RE = re.compile(r"\{.*?\}|\[.*?\]", re.DOTALL)
