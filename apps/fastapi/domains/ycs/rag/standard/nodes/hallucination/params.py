"""ycs/rag/standard/nodes/hallucination — per-doc input cap."""
from __future__ import annotations


# Truncate each source document at this many chars when feeding the
MAX_DOC_CHARS = 1000

# Hallucination-judge budget (2026-09-15: 60 → 45s) — failure defaults
# to grounded=True (non-blocking), so fail fast.
HALLUCINATION_TIMEOUT_S = 45.0

# Truthy/falsy literals for lenient bool coercion of judge output.
TRUTHY = {"true", "1", "yes", "y", "on", True, 1}
FALSY  = {"false", "0", "no", "n", "off", False, 0}
