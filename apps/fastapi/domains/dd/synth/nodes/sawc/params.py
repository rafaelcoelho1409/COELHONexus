"""sawc — tunable scalars (v2 cookbook schema limits + draft-of-N + memory)."""
from __future__ import annotations


# v2 schema : cookbook-style nested structure where
# each Subtopic is a 1:1 pair of (explanation, code block).
SUBTOPICS_MIN = 3
SUBTOPICS_MAX = 12
SUBHEADING_MIN_WORDS = 1    # API-reference subtopics are named by a bare identifier ('asyncio.BoundedSemaphore') — a 2-word floor rejected whole drafts over it
SUBHEADING_MAX_WORDS = 10
EXPLANATION_WORDS_MIN = 8
EXPLANATION_WORDS_MAX = 80
INTRO_CHARS_MIN = 20
INTRO_CHARS_MAX = 400

# Best-of-N drafts.
N_DRAFTS = 2
MAX_REPAIR_ATTEMPTS = 2

# Legacy paragraph-mode bounds (kept for backwards-compat).
PARAGRAPHS_MIN = 2
PARAGRAPHS_MAX = 12
PARAGRAPH_CHARS_MIN = 80
PARAGRAPH_CHARS_MAX = 1800
HEADING_MIN_WORDS = 1    # matches outline_sdp — single-word module/API names are valid H2s
HEADING_MAX_WORDS = 8
CODE_REFS_MAX = 30
CITATIONS_MIN = 0
CITATIONS_MAX = 12
CITATION_CLAIM_CHARS_MIN = 6
CITATION_CLAIM_CHARS_MAX = 400
PLACEMENT_HINT_CHARS_MIN = 4
PLACEMENT_HINT_CHARS_MAX = 200
MEMORY_TERMS_MIN = 0
MEMORY_TERMS_MAX = 12
MEMORY_TERM_CHARS_MIN = 2
MEMORY_TERM_CHARS_MAX = 80
MEMORY_SUMMARY_CHARS_MIN = 40
MEMORY_SUMMARY_CHARS_MAX = 600

BLOB_PREFIX = "synth"

# Section-relevance code routing. The digest routes a source to the section(s)
# it is mostly about, so every block of a multi-topic guide page lands in ONE
# section and the others — given 0 routed hashes — go prose-only even though the
# chapter holds on-topic code (elasticsearch-python ch-11: 31 blocks → s1, 0 → s2-s4).
# A section with too few routed blocks also considers chapter-wide blocks whose
# vocabulary the section's own text covers at least this much …
RELEVANT_PAD_MIN_SCORE  = 0.35
# … and only leaves prose mode when this many such blocks exist (< this and
# the section stays prose rather than being handed loosely related code).
RELEVANT_PAD_MIN_BLOCKS = 3
RELEVANCE_QUERY_MAX_CHARS = 30000
