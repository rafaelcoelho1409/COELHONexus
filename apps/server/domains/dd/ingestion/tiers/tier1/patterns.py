"""Tier 1 — pre-compiled regex for manifest detection."""
from __future__ import annotations

import re


FENCE_RE = re.compile(r"(?m)^```")
URL_LINE_RE = re.compile(r"(?m)^URL:\s+https?://")
MD_POINTER_RE = re.compile(r"(?m)^Markdown:\s+https?://\S+\.md\s*$")
