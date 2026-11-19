"""Compiled patterns used by BD Query's Cypher safety checks."""
from __future__ import annotations

import re

CYPHER_WRITE_PROCEDURE = re.compile(
    r"\bcall\s+("
    r"apoc\.(create|merge|refactor|nodes\.delete|periodic|trigger|atomic)|"
    r"db\.(create|drop|index\.fulltext\.(create|drop))|"
    r"gds\..*\.(write|mutate)|"
    r"dbms\."
    r")",
    flags = re.IGNORECASE,
)
CYPHER_STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`")
CYPHER_LINE_COMMENT = re.compile(r"//[^\n]*")
CYPHER_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", flags = re.DOTALL)
CYPHER_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
CYPHER_READ_QUERY_START = re.compile(
    r"^(?:optional\s+match|match|with|unwind|return|"
    r"call\s+(?:db\.(?:labels|relationshiptypes|propertykeys|schema\.)|apoc\.meta\.))\b",
    flags = re.IGNORECASE,
)
CYPHER_LIMIT_VALUE = re.compile(r"\blimit\s+(\d+)\b", flags = re.IGNORECASE)
