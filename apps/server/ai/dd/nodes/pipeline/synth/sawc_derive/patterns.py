"""sawc_derive — pre-compiled regex for signatures, fences, imports, and capstones."""
from __future__ import annotations
from . import params

import re


# Catches signature-only vault entries: bare function signatures and `def foo(...): ...` one-liners (the ch03 thin-block pattern).
SIGNATURE_ONLY_RE = re.compile(
    r"""
    ^\s*
    (?:def\s+|async\s+def\s+)?       # optional def keyword
    \w+\s*                            # function name
    \(.*?\)                           # arg list
    (?:\s*->\s*[\w\[\],\s\|\.]+)?    # optional return annotation
    \s*[:.]?\s*                       # optional trailing : or .
    $
    """,
    re.VERBOSE,
)

# Fenced-code-block extractor.
FENCE_RE = re.compile(
    r"```(?:[a-zA-Z0-9_+\-]*)\n(.*?)\n```",
    re.DOTALL,
)

# Imports detector for the structural scorer.
IMPORT_RE = re.compile(r"^\s*(?:from\s+\w+|import\s+\w+)")

# Lone-ellipsis detector ("..." on its own line, placeholder hallmark).
LONE_ELLIPSIS_RE = re.compile(r"^\s*\.{3}\s*$", re.MULTILINE)

# JSON envelope extractor for the re-explain call.
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Identifier extraction used to compare capstone code with the APIs taught by each step.
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Remove the optional heading prefix from a generated capstone title.
CAPSTONE_TITLE_PREFIX_RE = re.compile(
    rf"^{re.escape(params.CAPSTONE_PREFIX)}\s*:?\s*",
    re.IGNORECASE,
)
