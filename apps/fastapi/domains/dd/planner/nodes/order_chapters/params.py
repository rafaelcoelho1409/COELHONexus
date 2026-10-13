"""order_chapters tunables — sampling + foundational keyword set."""
from __future__ import annotations


BLOB_PREFIX = "planner"

# Number of independent LLM ordering samples to draw before Borda
# aggregation. 3 is the USC sweet spot: enough diversity to reveal
# disagreement, cheap enough on free tiers (3 calls/study at ~10s each).
# 2026-09-27: 3 -> 5. Different rationale than chapter_propose's N_SAMPLES
# bump — this is rank AGGREGATION (Borda count), and more independent
# rankings genuinely converge closer to the true consensus order (reduces
# how much any one sample's idiosyncratic ranking skews the result), a
# well-established property of aggregation methods generally.
N_SAMPLES = 5
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
# 2026-09-27: 3 -> 5, paired with N_SAMPLES above — otherwise 5 samples
# would run in batches of 3 instead of fully parallel.
SAMPLE_CONCURRENCY = 5

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

# 2026-09-08: 20.0 -> 130.0. Same fix as doc_distill/chapter_assign's
# SETTLE_DELAY_S — 20s was far shorter than the Rotator Router's own
# cooldown_time=120 (chain/service.py's _get_router()), so it never
# reliably outlasted a deployment benched near the end of chapter_assign's
# run (chapter_select in between makes no LLM calls, so no extra recovery
# time there). 130s (cooldown_time + 10s buffer) actually guarantees it.
SETTLE_DELAY_S = 130.0

# The docs' own navigation order is the strongest free ordering signal (authors
# sequence their pages pedagogically). It votes as this many extra rankings next
# to the LLM samples in the Borda aggregation, and is the fallback order when
# every LLM sample fails (instead of arbitrary chapter-index order).
SOURCE_ORDER_WEIGHT = 2
# ... but only when the idx order carries the authors' sequence. A single-URL llms-full dump, or a
# sitemap listed alphabetically (fastmcp 99% and deepagents 100% of neighbouring pages in ascending
# URL order; asyncio 42%), is just sorted paths — as votes they put "Client Groups" first.
SOURCE_ORDER_MIN_PATHS = 3
SOURCE_ORDER_ALPHA_MAX = 0.9
# Foundational-prefix rule: these patterns pin the chapter to position 0 regardless of LLM ordering; only the FIRST match anchors.
# (2026-10-13: "cli"/"command line" removed — a CLI *tooling* chapter is reference material, not setup; fastmcp's "FastMCP CLI Tooling" opened the book.)
FOUNDATIONAL_KEYWORDS = (
    "install",
    "installation",
    "setup",
    "getting started",
    "quickstart",
    "first steps",
)
