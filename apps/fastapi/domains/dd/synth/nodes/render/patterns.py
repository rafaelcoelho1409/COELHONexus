"""render — pre-compiled regex (vault-sentinel detection, identifier
extraction)."""
from __future__ import annotations

import re


# Sentinel pattern from `synth/vault.py:_make_sentinel`. Used to scan
# the rendered output for ANY unresolved sentinels (would indicate a
# materialization bug). Lang attribute is optional (vault.py emits it
# only when lang is non-empty).
SENTINEL_RE = re.compile(
    r'<code-ref hash="([0-9a-f]{16})"(?:\s+lang="[^"]*")?\s*/>'
)

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
