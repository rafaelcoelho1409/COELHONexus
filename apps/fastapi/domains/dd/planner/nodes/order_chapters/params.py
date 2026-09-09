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
# response. Was 800 ("fits ~16 chapters comfortably", but chapter_select can
# still hand this node more, and 800 leaves ~0 headroom for a reasoning
# model's <think> preamble — same failure class confirmed in off_topic/
# doc_distill/chapter_assign).
MAX_TOKENS = 1200
# How many characters of each chapter description to include in the
# prompt. Two sentences is enough context for ordering; longer wastes
# tokens.
DESCRIPTION_CHARS = 240
# Concurrency for the N parallel sample calls.
SAMPLE_CONCURRENCY = 3

# 2026-09-08: 60s -> 120s -> 70s. First raised to 120s using Synth's own
# percentile numbers by analogy — did not fix this node (still 100% failed
# across every run since, first via RouterRateLimitError pool exhaustion,
# then via genuine APITimeoutError once that was addressed elsewhere).
# Pulling THIS node's own 14-day Langfuse percentiles showed genuine
# successful orderings top out at p99=50.3s, max=54.9s — this node's calls
# (ranking N chapter titles+descriptions) don't need anywhere near 120s.
# The 120s-ceiling timeouts observed late in a full run more likely
# reflect pool fatigue after ~35+ minutes of sustained upstream traffic
# from the nodes before it than a genuine need for more per-call time — a
# dead call this late should fail fast, not hang for the old ceiling.
TIMEOUT_S = 70.0

# 2026-09-08: this node runs right after chapter_assign's own heavy burst
# (chapter_select in between makes no LLM calls). Neither the timeout raise
# nor the Router's loosened allowed-fails tolerance fixed it — both
# consecutive live runs still saw all 3 samples fail with
# RouterRateLimitError "No deployments available", consistent with genuine
# upstream provider RPM exhaustion rather than a local circuit-breaker
# false positive. A short settle window gives rolling per-minute quotas a
# chance to partially refill before this node's own fan-out begins.
SETTLE_DELAY_S = 20.0

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
