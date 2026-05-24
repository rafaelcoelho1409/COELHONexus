"""checklist_eval constants — tunables, version strings, LLM criteria keys."""
from __future__ import annotations


# =============================================================================
# Versioning + tunables
# =============================================================================
CHECKLIST_SCHEMA_VERSION = "1.0"
CHECKLIST_PROMPT_VERSION = "v1-2026-05-19"

_PASS_THRESHOLD = 0.80
_DENSITY_MIN_CHARS_PER_PARA = 150
_DENSITY_MAX_CHARS_PER_PARA = 1200
_REPAIR_RATE_MAX = 0.50
_PICKER_FALLBACK_RATE_MAX = 0.50
_MIN_CITATIONS_PER_SECTION = 1
_MAX_RENDERED_CHAPTER_CHARS = 60_000
_FEEDBACK_MIN_CHARS = 4
_FEEDBACK_MAX_CHARS = 600

# Ship #3 (2026-05-24, code-first goal) — code density gate. The user's
# stated KD goal is concise CODE-RICH learning material, not 400-page
# summaries. This criterion fails chapters that emit too few code_refs
# given the available vault budget. When it fails inside the mgsr→sawc
# loop (already shipped), sawc re-rolls with fresh drafts → eventually
# converges on code-dense output.
_MIN_AVG_CODE_REFS_PER_SECTION = 2.0   # average across sections
_MIN_CODE_REF_COVERAGE_FRACTION = 0.5  # fraction of allowed_hashes cited

# Names of the LLM-judge criteria — used both as keys in the LLM JSON
# output AND as identifiers in CriterionResult.name. Adding/removing
# from here requires a prompt_version bump.
_LLM_CRITERIA = (
    "chapter_reads_coherently",
    "claims_grounded_in_sources",
    "terminology_consistent",
    "prose_code_first_not_meta_framing",
    "code_refs_introduced_in_prose",
)
