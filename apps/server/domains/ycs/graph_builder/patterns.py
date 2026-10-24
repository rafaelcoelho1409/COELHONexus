"""graph_builder patterns — pre-compiled regexes (no behavior)."""
from __future__ import annotations

import re


# Pre-compiled regex for collapsing internal whitespace (multiple
# spaces / tabs / newlines → single space). Module-level so we don't
# re-compile per call inside the resolve_entities hot path.
WS_RE = re.compile(r"\s+")
