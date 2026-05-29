"""sawc constants — module-level variables only."""
from __future__ import annotations

import re


# =============================================================================
# Versioning + tunables
# =============================================================================
SAWC_SCHEMA_VERSION = "2.0-cookbook"
# U7 (2026-05-28) — bumped to invalidate SAWC cache after shipping:
#   - U2 cross-section vault-hash dedup (sawc/node.py)
#   - U7 per-section source-doc binding (sawc/node.py)
#   - upstream U3 H2 cap tightening + U6 semantic H2 dedup (outline)
# These together change the inputs the writer sees per section, so
# stale cached drafts no longer reflect the intended structure.
# v5 (2026-05-29) — rule 9b "no boilerplate recycling" added to the writer
# prompt (DD-SYNTH-SECTION-RECYCLING-2026-05-29 fix #3). Pairs with the
# render-time cross-section body dedup; bumped so the re-run regenerates
# drafts under the new instruction.
SAWC_PROMPT_VERSION = "v5-no-recycle-2026-05-29"

# v2 schema (2026-05-24 evening): replaces flat paragraphs + code_refs with
# a cookbook-style {intro, subtopics: [{subheading, explanation, code_ref_hash}]}
# structure. Forces 1:1 pairing of explanation→code via Pydantic. See
# docs/KD-CODE-FIRST-IMPLEMENTATION-2026-05-24.md.
_SUBTOPICS_MIN = 3
_SUBTOPICS_MAX = 12
_SUBHEADING_MIN_WORDS = 2
_SUBHEADING_MAX_WORDS = 10
_EXPLANATION_WORDS_MIN = 8
_EXPLANATION_WORDS_MAX = 80
_INTRO_CHARS_MIN = 20
_INTRO_CHARS_MAX = 400

# Bumped 2026-05-25 from 3 → 2. MAMM-Refine (arXiv 2503.15272) recommends
# N=3 best-of-N, but empirically on the FastMCP corpus the pairwise critic-
# picker tiebreaks on structural_score 80%+ of the time once Ship B/E
# validators are active — the third draft rarely wins. Cutting to N=2
# saves 33% of sawc's LLM-call budget (~5 min/iter on a 15-min sawc pass)
# without measurable quality loss. The pairwise picker handles N=2 with a
# single match (already has `if len(candidates) <= 1: return 0,…` short-
# circuit, so N=2 hits the standard knockout path with one round).
_N_DRAFTS = 2
# 2026-05-26 evening (CORR-1, post-Browser-Use-Run-2): reverted 1 → 2.
# Empirical: with B4 active, ch-01 had 9/12 sections ship with non-empty
# `issues` lists (failed `all_sections_present` despite no placeholders),
# ch-02 had 19/20. The repair rate also went UP (37-57% vs 27% baseline)
# suggesting the json_schema response_format (B1) was producing
# Pydantic-rejected outputs the single repair couldn't close. Two repair
# attempts restore the previous tolerance for tricky sections; the
# response_format softening (CORR-2) in sawc/node.py reduces the input
# pressure into the loop.
_MAX_REPAIR_ATTEMPTS = 2
_PARAGRAPHS_MIN = 2
_PARAGRAPHS_MAX = 12
_PARAGRAPH_CHARS_MIN = 80
_PARAGRAPH_CHARS_MAX = 1800
_HEADING_MIN_WORDS = 2
_HEADING_MAX_WORDS = 8
_CODE_REFS_MAX = 30
_CITATIONS_MIN = 0
_CITATIONS_MAX = 12
_CITATION_CLAIM_CHARS_MIN = 6
_CITATION_CLAIM_CHARS_MAX = 400
_PLACEMENT_HINT_CHARS_MIN = 4
_PLACEMENT_HINT_CHARS_MAX = 200
_MEMORY_TERMS_MIN = 0
_MEMORY_TERMS_MAX = 12
_MEMORY_TERM_CHARS_MIN = 2
_MEMORY_TERM_CHARS_MAX = 80
_MEMORY_SUMMARY_CHARS_MIN = 40
_MEMORY_SUMMARY_CHARS_MAX = 600

_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_SECTION_ID_RE = re.compile(r"^s\d{1,3}$")
