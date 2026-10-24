"""ycs/query — pre-compiled regexes for the Cypher read-only safety guard.
"""
from __future__ import annotations

import re


# APOC write surface — anything under `apoc.create.*`, `apoc.merge.*`,
# `apoc.refactor.*`, `apoc.periodic.iterate(... CREATE ...)` etc. Plus
# GDS catalog mutate calls. Plain `apoc.meta.*` / `db.labels()` / etc.
# are allowed (they're read paths).
CYPHER_WRITE_PROC = re.compile(
    r"\bcall\s+("
    r"apoc\.(create|merge|refactor|nodes\.delete|periodic|trigger|atomic)|"
    r"db\.(create|drop|index\.fulltext\.(create|drop))|"
    r"gds\..*\.(write|mutate)|"
    r"dbms\."
    r")",
    flags = re.IGNORECASE,
)

# Match a write-keyword as a WHOLE TOKEN (word boundaries) outside of
# string literals — see `domain.assert_cypher_readonly`. Cypher escapes
# are `\"` / `\\` etc.
CYPHER_STRING = re.compile(
    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`",
)
