"""ycs/rag — pre-compiled regexes shared by both the standard and
adaptive graphs."""
from __future__ import annotations

import re


THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>\s*")
