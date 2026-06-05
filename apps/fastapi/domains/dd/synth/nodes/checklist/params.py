"""checklist_eval — tunable thresholds + LLM-judge criterion names."""
from __future__ import annotations


PASS_THRESHOLD = 0.80

# v1 schema (paragraph-mode) — kept for backwards-compat with any legacy
# blobs still emitting avg_chars_per_paragraph in coverage_stats.
DENSITY_MIN_CHARS_PER_PARA = 150
DENSITY_MAX_CHARS_PER_PARA = 1200
# v2 cookbook schema (2026-05-24 PM): density is measured in
# explanation-words-per-subtopic, NOT chars-per-paragraph.
DENSITY_MIN_AVG_EXPLANATION_WORDS = 12.0
DENSITY_MAX_AVG_EXPLANATION_WORDS = 70.0

REPAIR_RATE_MAX = 0.50
PICKER_FALLBACK_RATE_MAX = 0.50
MIN_CITATIONS_PER_SECTION = 1
MAX_RENDERED_CHAPTER_CHARS = 60_000
FEEDBACK_MIN_CHARS = 4
FEEDBACK_MAX_CHARS = 600

# Ship #3 (2026-05-24, code-first goal) — code density gate.
MIN_AVG_CODE_REFS_PER_SECTION = 2.0   # average across sections
MIN_CODE_REF_COVERAGE_FRACTION = 0.5  # fraction of allowed_hashes cited

# Names of the LLM-judge criteria — used both as keys in the LLM JSON
# output AND as identifiers in CriterionResult.name. Adding/removing
# from here requires a prompt_version bump.
LLM_CRITERIA = (
    "chapter_reads_coherently",
    "claims_grounded_in_sources",
    "terminology_consistent",
    "prose_code_first_not_meta_framing",
    "code_refs_introduced_in_prose",
)

BLOB_PREFIX = "synth"
