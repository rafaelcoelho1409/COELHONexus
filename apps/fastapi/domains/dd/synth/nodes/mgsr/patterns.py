"""mgsr — pre-compiled regex (section-id format + JSON envelope)."""
from __future__ import annotations

import re


SECTION_ID_RE = re.compile(r"^s\d{1,3}$")
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
