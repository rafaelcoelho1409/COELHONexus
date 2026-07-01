"""render — pre-compiled regex (vault-sentinel detection, identifier
extraction)."""
from __future__ import annotations

import re


# Scans rendered output for unresolved sentinels (materialization bug). Lang attr is optional — vault emits it only when lang is non-empty.
SENTINEL_RE = re.compile(
    r'<code-ref hash="([0-9a-f]{16})"(?:\s+lang="[^"]*")?\s*/>'
)

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
