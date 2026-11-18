"""Tier 3 — pre-compiled regexes for URL paths."""
from __future__ import annotations

import re


# A path segment that is a year (`/2024/`): a date in a blog URL, not a release of the docs.
YEAR_RE = re.compile(r"^(?:19|20)\d\d$")

# A run of digits: the numbers of a release folder (`v5.21.0` → 5, 21, 0).
DIGITS_RE = re.compile(r"\d+")

# A run of anything a slug does not keep.
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
