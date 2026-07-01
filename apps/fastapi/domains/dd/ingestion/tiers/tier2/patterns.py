"""Compiled regex for llms.txt: LINK_MD_RE for [title](url), LINK_BARE_RE for bare-URL bullet `title: https://...` (Supervision style)."""
from __future__ import annotations

import re


LINK_MD_RE = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)]+)\)", re.MULTILINE)
LINK_BARE_RE = re.compile(
    r"^\s*[-*]\s+(.+?):\s+(https?://\S+)\s*$", re.MULTILINE,
)
