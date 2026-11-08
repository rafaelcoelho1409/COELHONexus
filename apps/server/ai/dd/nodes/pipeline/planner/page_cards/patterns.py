"""page_cards — pre-compiled regex patterns."""
from __future__ import annotations

import re


REFERENCE_PATH_RE = re.compile(r"(?:^|/)(?:api|reference|refs?|sdk)(?:/|$)", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*#*\s*$", re.MULTILINE)
