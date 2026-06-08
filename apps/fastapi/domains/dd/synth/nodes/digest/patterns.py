"""digest_construct — pre-compiled regex (hash/section-id format + vault
sentinel extraction)."""
from __future__ import annotations

import re


# 16-hex hash format (matches vault.py sentinels)
HASH_RE = re.compile(r"^[0-9a-f]{16}$")
SECTION_ID_RE = re.compile(r"^s\d{1,3}$")

# Hash-only matcher — mirrors vault/patterns.py SENTINEL_HASH_RE. The
# strict `"\s*/>` anchor that was here (pre-2026-06-08) failed to match
# every sentinel with a `lang="…"` attribute, which is the default vault
# emits for any fenced block with a language tag (`<code-ref hash="X"
# lang="python"/>`). Result: `extract_vault_hashes` returned [] for
# every section → `valid_hash_set` empty → digest LLM forced to emit
# `code_refs=[]` via repair → SAWC saw `n_routed_hashes=0` everywhere →
# render had nothing to materialize → final chapter had zero code blocks.
VAULT_HASH_IN_TEXT_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"')
