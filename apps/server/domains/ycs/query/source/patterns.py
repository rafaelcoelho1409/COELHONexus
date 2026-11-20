"""Pre-compiled patterns for YCS Query validation and output cleanup."""
from __future__ import annotations

import re

CYPHER_WRITE_PROC = re.compile(
    r"\bcall\s+("
    r"apoc\.(create|merge|refactor|nodes\.delete|periodic|trigger|atomic)|"
    r"db\.(create|drop|index\.fulltext\.(create|drop))|"
    r"gds\..*\.(write|mutate)|"
    r"dbms\."
    r")",
    flags = re.IGNORECASE,
)
CYPHER_STRING = re.compile(
    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`",
)
CYPHER_LINE_COMMENT = re.compile(r"//[^\n]*")
CYPHER_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", flags = re.DOTALL)
CYPHER_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
CYPHER_LIMIT_VALUE = re.compile(r"\blimit\s+(\d+)\b")
CYPHER_FENCE = re.compile(
    r"```(?:cypher|cql)?\s*\n?(.*?)```",
    flags = re.DOTALL | re.IGNORECASE,
)
CYPHER_LINE_START = re.compile(
    r"^\s*(?:"
    r"MATCH\s*\("                       # MATCH (
    r"|OPTIONAL\s+MATCH\s*\("           # OPTIONAL MATCH (
    r"|WITH\s+\S"                       # WITH x
    r"|UNWIND\s+\S"                     # UNWIND list
    r")",
    flags = re.IGNORECASE,
)
CYPHER_QUERY_START = re.compile(
    r"\b(?:MATCH|OPTIONAL\s+MATCH)\s*\(",
    flags = re.IGNORECASE,
)
CYPHER_FORMAT_PROTECTED_SPAN = re.compile(
    r"//[^\n]*"                         # line comment
    r"|/\*.*?\*/"                       # block comment (non-greedy)
    r"|'(?:\\.|[^'\\])*'"               # single-quoted string
    r"|\"(?:\\.|[^\"\\])*\""           # double-quoted string
    r"|`(?:\\.|[^`\\])*`",              # back-tick identifier
    flags = re.DOTALL,
)
CYPHER_FORMAT_CLAUSE = re.compile(
    r"(?<!\n)[ \t]+(OPTIONAL\s+MATCH|UNION\s+ALL|ORDER\s+BY|MATCH|WHERE|WITH|RETURN|LIMIT|SKIP|UNION|UNWIND|CALL|YIELD)\b",
    flags = re.IGNORECASE,
)
CYPHER_PLACEHOLDER = re.compile(r"\x00P(\d+)\x00")
CYPHER_BLANK_LINES = re.compile(r"\n{3,}")
JSON_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
