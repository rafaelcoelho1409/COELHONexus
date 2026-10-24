"""Pre-compiled regexes for the RR agent."""
from __future__ import annotations

import re


SCAN_ID_RE: re.Pattern[str] = re.compile(r"scan_id=([0-9a-fA-F-]{32,})")
