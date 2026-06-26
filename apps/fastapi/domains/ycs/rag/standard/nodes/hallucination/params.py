"""ycs/rag/standard/nodes/hallucination — per-doc input cap."""
from __future__ import annotations


# Truncate each source document at this many chars when feeding the
MAX_DOC_CHARS = 1000
