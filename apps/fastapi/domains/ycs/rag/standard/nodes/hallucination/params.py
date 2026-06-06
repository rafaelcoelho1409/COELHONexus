"""ycs/rag/standard/nodes/hallucination — per-doc input cap."""
from __future__ import annotations


# Truncate each source document at this many chars when feeding the
# judge. Mirror of deprecated `rag.py:L122`."""
MAX_DOC_CHARS = 1000
