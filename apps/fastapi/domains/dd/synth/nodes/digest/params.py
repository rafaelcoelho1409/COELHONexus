"""digest_construct — Pydantic-enforced limits + source-pool merge thresholds."""
from __future__ import annotations


MAX_KEY_FACTS_PER_CONTRIB = 5
MIN_KEY_FACTS_PER_CONTRIB = 1
MAX_CONTRIBS_PER_SOURCE = 20
OVER_SPREAD_THRESHOLD = 2

MERGE_JACCARD = 0.60
MERGE_CONTAINMENT = 0.80
# Sections with fewer than this many distinct primaries are folded under containment-merge.
MERGE_MIN_PRIMARY_TO_DEFEND = 2
# Two sections are never folded into one that would hold more routed code blocks than a section can print (sawc caps a topped-up
# section at TOPUP_SECTION_MAX subtopics): asyncio ch-05 folded "Network Servers" into a section already routed 24 of the chapter's
# 35 blocks, and the chapter printed 17.
MERGE_MAX_BLOCKS = 14

SUMMARY_MIN_CHARS = 20
SUMMARY_MAX_CHARS = 600
KEY_FACT_MIN_CHARS = 6
KEY_FACT_MAX_CHARS = 300
OVERALL_SUMMARY_MIN_CHARS = 30
OVERALL_SUMMARY_MAX_CHARS = 800
SOURCE_TITLE_MIN_CHARS = 3
SOURCE_TITLE_MAX_CHARS = 200

BLOB_PREFIX = "synth"


# Relevance-rank order for source routing (lower wins).
RELEVANCE_RANK = {"primary": 0, "supporting": 1, "tangential": 2}


CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)
