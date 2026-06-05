"""chapter_propose — pre-compiled regex (JSON, headings, CLI namespaces)."""
from __future__ import annotations

import re


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
H2_RE = re.compile(r"(?m)^\s{0,3}#{1,2}\s+(.+?)$")
CLI_PATTERN_RE = re.compile(
    r"(?:commands?|subcommands?|cli)/([a-z][a-z0-9-]*)",
    re.IGNORECASE,
)
