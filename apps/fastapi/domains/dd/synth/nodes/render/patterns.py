"""render — pre-compiled regex (vault-sentinel detection, identifier
extraction)."""
from __future__ import annotations

import re


# Scans rendered output for unresolved sentinels (materialization bug). Lang attr is optional — vault emits it only when lang is non-empty.
SENTINEL_RE = re.compile(
    r'<code-ref hash="([0-9a-f]{16})"(?:\s+lang="[^"]*")?\s*/>'
)

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# Recovery: LLM ignores "NO fences" instruction and returns prose + nested code block; pull the largest inner fenced block body (observed ch-03 browser-use run).
INNER_FENCE_RE = re.compile(
    r'(?P<open>```+|~~~+)(?P<info>[^\n]*)\n(?P<body>.*?)\n(?P=open)',
    re.DOTALL,
)

# vault fence_text = full fenced block; normalize ONLY the body so info-string + markers are byte-preserved regardless of LLM output.
FENCE_RE = re.compile(
    r'^(?P<open>```+|~~~+)(?P<info>[^\n]*)\n'
    r'(?P<body>.*?)'
    r'\n(?P<close>```+|~~~+)\s*$',
    re.DOTALL,
)
