"""Pre-compiled chapter-id pattern (synth chapter keys look like `ch-03`)."""
from __future__ import annotations

import re


CHAPTER_ID_RE = re.compile(r"^ch-(\d+)")
