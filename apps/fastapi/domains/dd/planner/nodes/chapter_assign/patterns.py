"""chapter_assign — pre-compiled regex."""
from __future__ import annotations

import re


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
FB_WORD_RE = re.compile(r"[a-z0-9]{3,}")
