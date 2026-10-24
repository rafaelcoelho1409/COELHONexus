"""langfuse patterns — pre-compiled regexes (no behavior)."""
from __future__ import annotations

import re


# LangFuse metadata keys must be safe path segments.
SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
