"""Tier 2 — pre-compiled regex for llms.txt link parsing.

Two link styles seen in llms.txt files:
  A) `- [Title](https://url)`           — canonical markdown link (most sites)
  B) `- Title (extra): https://url`     — bare-URL bullet (Supervision, others)
      also matches `- Title: https://url` (no parens)
"""
from __future__ import annotations

import re


LINK_MD_RE = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)]+)\)", re.MULTILINE)
LINK_BARE_RE = re.compile(
    r"^\s*[-*]\s+(.+?):\s+(https?://\S+)\s*$", re.MULTILINE,
)
