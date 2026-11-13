"""Compiled patterns for page-audit scans and finding validation."""
from __future__ import annotations

import re


FENCE_RE = re.compile(r"(`{3,}|~{3,})")
HEADING_WITHOUT_SPACE_RE = re.compile(r"#{1,6}[A-Za-z]")
EMPTY_HEADING_RE = re.compile(r"#{1,6}\s*$")
# UTF-8 text read as Latin-1/Windows-1252 (`Ã©`, `Â `, `â€™`), and the replacement character.
MOJIBAKE_RE = re.compile("[ÃÂ][\u0080-¿]|â€.|�")
EMPTY_TABLE_ROW_RE = re.compile(r"\s*\|(?:\s*\|)?\s*")

EXPORT_CONVENTION_RE = re.compile(
    r"\A\s*(?:<[A-Z][A-Za-z]*\b|<(?:img|iframe|video)\b|(?:`{3,}|~{3,})[^\n]*\btheme="
    r"|>(?:[ \t]*>)*[ \t]*\[(?i:tab|card|step|accordion|!(?:note|tip|warning|caution|important|info|check))\])"
)
TABLE_DELIMITER_RE = re.compile(r"\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?")
GITBOOK_COMPONENT_NAMES = frozenset({
    "stepper", "step", "tabs", "tab", "columns", "column", "file", "code",
    "embed", "content-ref", "hint", "update", "callout", "expand", "details",
})
GITBOOK_COMPONENT_TAG_RE = re.compile(
    r"\{%\s*(?P<end>end)?(?P<name>stepper|step|tabs?|columns?|column|file|code|embed|content-ref|hint|update|callout|expand|details)\b[^%]*%\}",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9:-]*)\b[^<>]*?/?>")
HTML_TAG_ONLY_RE = re.compile(
    r"\A\s*</?[A-Za-z][A-Za-z0-9:-]*(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'<>`]+))?)*\s*/?>\s*\Z",
    re.DOTALL,
)
HTML_NUMERIC_ENTITY_RE = re.compile(r"&#(?:[0-9]{1,7}|[xX][0-9a-fA-F]{1,6});")
ESCAPED_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9]+(?:\\_[A-Za-z0-9]+)+")
VOID_HTML_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
ATX_HEADING_LINE_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
ATX_CLOSING_HASHES_RE = re.compile(r"#+")
BLOCKQUOTE_LINE_RE = re.compile(r"^ {0,3}>")
FENCED_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.DOTALL)
MODEL_CONFIGURATION_LINE_RE = re.compile(r"(?m)^(\s*model\s*=\s*)(?:\"([^\"]*)\"|'([^']*)')")
