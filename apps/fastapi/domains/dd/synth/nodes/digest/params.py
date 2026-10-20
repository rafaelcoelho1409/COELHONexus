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


# Per-source LLM calls (draft + repair).
# was 24 (issue #18, 2026-09-07): the comment below ("FGTS-VA bandit
# absorbs 429s") predates the Sept 4 migration to the standalone Rotator
# microservice — that in-process bandit no longer exists here. The
# Rotator's own _PROVIDER_CAPS sum to ~19 concurrent slots across every
# provider combined; 24 alone already exceeds total system capacity
# before any other node's calls compete for the same slots. Lowered to
# match Planner's own ceiling (doc_distill/off_topic/chapter_propose all
# cap at 16 — none of Planner's nodes exceed the shared Rotator's total
# capacity the way this one did).
CONCURRENCY = 16    # max concurrent per-source LLM calls.
# Pause before the second wave: the arms that just stalled are ejected for tens of seconds to minutes, and an immediate retry meets the same
# ones (fastmcp ch-01: second wave recovered 1/1, 4/7, 0/4 and 0/1 sources over four runs).
WAVE_PAUSE_S = 45.0
# Expected ~7-9 min realistic (repair attempts + outliers); quality unchanged, pure throughput.
DRAFT_TEMPERATURE  = 0.1   # routing decisions should be ~deterministic
REPAIR_TEMPERATURE = 0.0
DRAFT_MAX_TOKENS   = 6000
REPAIR_MAX_TOKENS  = 6000
# chat_text_async's own default (30s) was undersized — confirmed
# live across 5 study runs (2026-09-05/07): digest_construct's per-source
# digestion routinely lost 40-60% of sources to APITimeoutError, driving
# a mandatory second wave nearly every chapter. Same fix as outline/sawc.
#   2026-09-08: raised 90s -> 120s. Percentile analysis (14-day Langfuse
#   trace data): successful-call max=89.3s, p99=81.9s — real successes
#   were stacking right up against the old 90s ceiling. Only ~10% of
#   failures are actually at-ceiling (rescuable by this change); the
#   other ~90% fail at a fixed ~30s Rotator-side ReadTimeout this
#   timeout can't reach — see SYNTH-PERFORMANCE-ANALYSIS-2026-09-07.md.
DRAFT_TIMEOUT_S = 120.0
REPAIR_TIMEOUT_S = 120.0
MAX_REPAIR_ATTEMPTS = 2
# Draft-call attempts before permanently losing this source's content (its
# key_facts/code_refs never reach any section — silent, not retried
# downstream). The Rotator's own ~40-arm cascade already exhausted by the
# time an exception surfaces here, so this only helps against a transient
# whole-pool wave (e.g. simultaneous cooldowns), not a deterministic failure.
MAX_CALL_ATTEMPTS = 2

# Per-source body cap — generous since each LLM sees ONLY one source.
# Most pages are <30K chars; cap at 100K to be safe.
MAX_SOURCE_CHARS = 100_000


# Relevance-rank order for source routing (lower wins).
RELEVANCE_RANK = {"primary": 0, "supporting": 1, "tangential": 2}


CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)

# A source holding more than CHUNK_MAX_BLOCKS code blocks is digested in chunks of about that many, each routed by its own call, and the results are merged: the model names
# only a few of the hashes of a page with dozens (deepagents ch-05 'subagents': 6 of 86 routed; langgraph ch-05: 28 of 149 blocks printed).
CHUNK_MAX_BLOCKS = 30
