"""Patterns for BD identifier contracts."""

from __future__ import annotations

import re

SAFE_SEGMENT_PATTERN = r"^[A-Za-z0-9_-]+$"
SAFE_SEGMENT_RE = re.compile(SAFE_SEGMENT_PATTERN)
