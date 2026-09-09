"""chapter_propose — pre-compiled regex (JSON, headings, CLI namespaces)."""
from __future__ import annotations

import re


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
H2_RE = re.compile(r"(?m)^\s{0,3}#{1,2}\s+(.+?)$")
CLI_PATTERN_RE = re.compile(
    r"(?:commands?|subcommands?|cli)/([a-z][a-z0-9-]*)",
    re.IGNORECASE,
)
# Trailing permalink-icon markdown link most static-site generators (mkdocs-
# material, Sphinx furo, etc.) append to every heading, e.g.
# `Check the Docs[¶](#check-the-docs "Permanent link")`. H2_RE captures the
# whole rest of the heading line, so without stripping this the raw markup
# leaks straight through — harmless while headings were only ever LLM prompt
# seeds (the LLM paraphrased it away), but _build_fallback_proposals() (2026-
# 09-04) started using these seeds verbatim as final chapter titles, making
# it a directly user-visible bug — confirmed live 2026-09-08 on the fastapi
# corpus (every fallback-path title carried its heading's permalink markup).
TRAILING_MD_LINK_RE = re.compile(r"\s*\[[^\]]*\]\([^)]*\)\s*$")

# 2026-09-08: H2_RE matches any line starting with 1-2 `#` chars, with no
# awareness of fenced code blocks — a Python comment like
# "# Code below omitted 👇" inside a ```python fence gets misread as a
# real markdown heading. Confirmed live on the fastapi corpus: this exact
# string became a fallback-generated chapter title. Stripped from the body
# BEFORE heading extraction runs (non-greedy, so multiple fences in one
# doc are each stripped individually rather than everything between the
# first and last fence).
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
