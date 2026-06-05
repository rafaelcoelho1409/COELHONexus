"""vault — pre-compiled sentinel regexes."""
from __future__ import annotations

import re


# Matches the canonical sentinel shape this module emits. `lang` is
# optional (older blocks with `lang=""` skip the attr); hash MUST be
# exactly 16 hex chars; self-closing form is mandatory.
SENTINEL_RE = re.compile(
    r'<code-ref hash="(?P<hash>[0-9a-f]{16})"(?: lang="(?P<lang>[^"]*)")?/>',
)
# Plain hash-only matcher used by `audit_roundtrip` to enumerate every
# sentinel-shaped token in an LLM output (whether or not it matches the
# vault — `invented` sentinels show up here).
SENTINEL_HASH_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"')
# Hash + ANY trailing attrs (used by `materialize` so unknown LLM-added
# attrs like `theme="..."` don't break restoration).
SENTINEL_ANY_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"[^/]*/>')
