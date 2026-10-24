"""plan_write — pre-compiled regex (slug normalization)."""
from __future__ import annotations

import re


SLUG_RE = re.compile(r"[^a-z0-9]+")
