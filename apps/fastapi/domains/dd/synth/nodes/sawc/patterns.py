"""sawc — pre-compiled regex (vault-hash format + section-id format)."""
from __future__ import annotations

import re


HASH_RE = re.compile(r"^[0-9a-f]{16}$")
SECTION_ID_RE = re.compile(r"^s\d{1,3}$")
