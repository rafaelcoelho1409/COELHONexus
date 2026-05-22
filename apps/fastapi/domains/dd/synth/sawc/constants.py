"""sawc constants — module-level variables only."""
from __future__ import annotations

import re


# =============================================================================
# Versioning + tunables
# =============================================================================
SAWC_SCHEMA_VERSION = "1.0"
SAWC_PROMPT_VERSION = "v1-2026-05-19"

_N_DRAFTS = 3
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
