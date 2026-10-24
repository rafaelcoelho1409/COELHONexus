"""Compiled regex for llms.txt (AnswerDotAI spec v2): LINK_MD_RE for `[title](url)` with optional `: notes` suffix, LINK_BARE_RE for bare-URL bullet `title: https://...` (Supervision style). H2_RE tracks `## Section` file-list grouping; QUOTE_RE collects `>` blockquote summary lines."""
from __future__ import annotations

import re


LINK_MD_RE = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)]+)\)(?:\s*:\s*(.+))?\s*$", re.MULTILINE)
LINK_BARE_RE = re.compile(
    r"^\s*[-*]\s+(.+?):\s+(https?://\S+)\s*$", re.MULTILINE,
)
H2_RE = re.compile(r"^\s*##\s+(.+?)\s*$", re.MULTILINE)
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$", re.MULTILINE)
# Redirect/archived-stub markers (mirror of Tier 1's manifest-shape guard, at
# page granularity): a fetched page that is only a pointer elsewhere.
REDIRECT_STUB_RES = (
    re.compile(r"archived version", re.IGNORECASE),
    re.compile(r"this page has moved", re.IGNORECASE),
    re.compile(r"moved permanently", re.IGNORECASE),
)
