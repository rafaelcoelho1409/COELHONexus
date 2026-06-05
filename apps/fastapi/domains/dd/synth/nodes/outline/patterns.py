"""outline_sdp — pre-compiled regex (section-id format)."""
from __future__ import annotations

import re


SECTION_ID_RE = re.compile(r"^s\d{1,3}$")   # s1, s2, ..., s999
