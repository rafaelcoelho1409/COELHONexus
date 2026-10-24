"""sawc — pre-compiled regex (vault-hash format + section-id format)."""
from __future__ import annotations

import re


HASH_RE = re.compile(r"^[0-9a-f]{16}$")
SECTION_ID_RE = re.compile(r"^s\d{1,3}$")

# Identifiers, flags and words of a code block — what two copies of one
# example have in common.
BLOCK_TOKEN_RE = re.compile(r"--[a-z][a-z0-9\-]*|[A-Za-z_][A-Za-z0-9_\.\-]+")

# A top-up subtopic that talks ABOUT the docs instead of stating their facts
# (asyncio ch-04: "The documentation provides examples of how to modify
# single-command scripts ..."): dropped.
META_FRAMING_RE = re.compile(
    r"^\s*(?:the|this)\s+(?:\w+\s+)?(?:documentation|docs|page|source|text|guide)\s+"
    r"(?:provides|shows|explains|describes|demonstrates|states|notes|mentions|covers|includes|contains|gives)\b",
    re.I,
)

# Greedy JSON blob (best-effort extraction from a fenced/prose response).
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
