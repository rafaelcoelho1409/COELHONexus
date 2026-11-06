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

# Fixed 2026-09-05 — a "/ plugin marketplace add ..." shipped with a
# stray space after the leading slash (not a valid Claude Code command;
# `/plugin` is). Origin is ambiguous — could be an LLM generation slip
# OR an artifact already present in the ingested source markdown — so
# this fixes it at render time regardless of where it came from, rather
# than relying only on prompting the writer not to do it. Scoped tight:
# only the first line of a fenced block, only a slash immediately
# followed by whitespace then a word — real code doesn't start a line
# that way (division operators appear mid-expression, not as the first
# two tokens of a line).
STRAY_SLASH_SPACE_RE = re.compile(r"^(/)[ \t]+(\w)")

# Fixed 2026-09-06 (issue #13) — vault/schemas.py deliberately preserves
# info_string "byte-exactly... carries Mintlify attrs" for audit fidelity
# (hash/drift checks are keyed on the code BODY, not the fence header), so
# this must NOT touch the stored vault entry — only the copy that actually
# reaches the rendered chapter. Confirmed live: `theme={null}` and a
# leaked framework-name token from the original MDX source appeared
# verbatim in a chapter's fence header twice in one run — a systematic
# MDX-authoring pattern (attrs on the fence's info line), not a fluke.
# `_lang_from_info` in render/service.py already extracts just the first
# token for internal use (choosing the AST validator, prompt building);
# this applies the same rule to what the reader actually sees.
FENCE_OPEN_RE = re.compile(r"^(```+|~~~+)([^\n]*)$")
