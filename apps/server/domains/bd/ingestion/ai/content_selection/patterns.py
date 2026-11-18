"""Regular expressions for recognizing printed contents pages."""
from __future__ import annotations

import re

CONTENTS_TITLE_RE = re.compile(
    r"^(?:(?:brief|detailed)\s+)?(?:table\s+of\s+)?contents"
    r"(?:\s+(?:in\s+detail|continued|\(continued\)))?\s*[:.]?$",
    re.IGNORECASE,
)
CONTENTS_LEADER_RE = re.compile(
    r"^\s*\S.{1,180}?(?:\.{2,}|…{2,}|·{2,}|_{2,}|\t+)\s*(?:\d{1,4}|[ivxlcdm]{1,8})\s*$",
    re.IGNORECASE,
)
CONTENTS_NUMBERED_ENTRY_RE = re.compile(
    r"^\s*(?:(?:chapter|part|appendix)\s+)?(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.)]?\s+\S.{2,140}?"
    r"\s{2,}(?:\d{1,4}|[ivxlcdm]{1,8})\s*$",
    re.IGNORECASE,
)
CONTENTS_TRAILING_PAGE_RE = re.compile(
    r"^\s*\S.{2,160}?\s{2,}(?:\d{1,4}|[ivxlcdm]{1,8})\s*$",
    re.IGNORECASE,
)
CONTENTS_TABLE_PAGE_RE = re.compile(
    r"^[.\s·…]*(?:\d{1,4}|[ivxlcdm]{1,8})[.\s·…]*$",
    re.IGNORECASE,
)
MARKDOWN_DECORATION_PREFIX_RE = re.compile(r"^\s{0,3}#{1,6}\s*|[*_`]")
