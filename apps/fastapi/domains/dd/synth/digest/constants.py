"""digest_construct constants — versioning, tunables, compiled regexes."""
from __future__ import annotations

import re


# =============================================================================
# Versioning + tunables
# =============================================================================
DIGEST_SCHEMA_VERSION = "1.0"
DIGEST_PROMPT_VERSION = "v1-2026-05-19"

_MAX_KEY_FACTS_PER_CONTRIB = 5
_MIN_KEY_FACTS_PER_CONTRIB = 1
_MAX_CONTRIBS_PER_SOURCE = 20
_OVER_SPREAD_THRESHOLD = 3
_SUMMARY_MIN_CHARS = 20
_SUMMARY_MAX_CHARS = 600
_KEY_FACT_MIN_CHARS = 6
_KEY_FACT_MAX_CHARS = 300
_OVERALL_SUMMARY_MIN_CHARS = 30
_OVERALL_SUMMARY_MAX_CHARS = 800
_SOURCE_TITLE_MIN_CHARS = 3
_SOURCE_TITLE_MAX_CHARS = 200

# 12-hex hash format (matches vault.py sentinels)
_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_SECTION_ID_RE = re.compile(r"^s\d{1,3}$")

_VAULT_HASH_IN_TEXT_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"\s*/>')
