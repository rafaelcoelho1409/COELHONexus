"""Text normalization patterns used by the book graph projection."""
from __future__ import annotations

import re

SPACE_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
