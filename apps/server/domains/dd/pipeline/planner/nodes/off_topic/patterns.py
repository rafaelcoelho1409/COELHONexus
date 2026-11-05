"""off_topic patterns — pre-compiled verdict-parser regexes (no behavior)."""
from __future__ import annotations

import re


THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
VERDICT_RE = re.compile(r"\b(KEEP|DROP)\b", re.IGNORECASE)
