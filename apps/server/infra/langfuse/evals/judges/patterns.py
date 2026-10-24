"""judges patterns — pre-compiled regexes (no behavior)."""
from __future__ import annotations

import re


# Rubric-judge responses must contain a single 1-5 integer; first match wins.
SCORE_RE = re.compile(r"[1-5]")
