"""Shared fetching — pre-compiled regex for the rate-limit headers."""
from __future__ import annotations

import re


# `RateLimit: "pages";r=99;t=109` (IETF draft: r = requests left in the window, t = seconds until it resets) and the older
# `RateLimit: limit=100, remaining=99, reset=109` spelling.
REMAINING_RE = re.compile(r"(?:\br|\bremaining)\s*=\s*(\d+)", re.IGNORECASE)
RESET_RE = re.compile(r"(?:\bt|\breset)\s*=\s*(\d+)", re.IGNORECASE)
