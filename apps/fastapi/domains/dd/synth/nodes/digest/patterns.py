"""digest_construct — pre-compiled regex (hash/section-id format + vault
sentinel extraction)."""
from __future__ import annotations

import re


# 16-hex hash format (matches vault.py sentinels)
HASH_RE = re.compile(r"^[0-9a-f]{16}$")
SECTION_ID_RE = re.compile(r"^s\d{1,3}$")

# Hash-only matcher — the strict `/>` anchor was wrong when lang="..." is present; must match <code-ref hash="X" lang="python"/> too.
VAULT_HASH_IN_TEXT_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"')
