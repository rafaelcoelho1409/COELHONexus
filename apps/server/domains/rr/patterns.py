"""Pre-compiled regexes for the RR domain."""
from __future__ import annotations

import re


ARXIV_ID_RE: re.Pattern[str] = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
TITLE_NORM_RE: re.Pattern[str] = re.compile(r"[^a-z0-9]+")
