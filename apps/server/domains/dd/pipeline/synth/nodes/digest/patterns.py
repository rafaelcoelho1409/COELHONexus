"""digest_construct — pre-compiled regex (hash/section-id format + vault
sentinel extraction)."""
from __future__ import annotations

import re


# 16-hex hash format (matches vault.py sentinels)
HASH_RE = re.compile(r"^[0-9a-f]{16}$")
SECTION_ID_RE = re.compile(r"^s\d{1,3}$")

# Hash-only matcher — the strict `/>` anchor was wrong when lang="..." is present; must match <code-ref hash="X" lang="python"/> too.
VAULT_HASH_IN_TEXT_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"')


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Identifier-ish word (a "word" for Jaccard/containment merges).
WORD_RE = re.compile(r"[a-z][a-z0-9_]{2,}")

# A markdown heading line (any level) — the cut points of a block split.
HEADING_LINE_RE = re.compile(r"^#{1,6} ", re.M)
