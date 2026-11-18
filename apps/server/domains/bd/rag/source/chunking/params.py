"""Stable passage-splitting policy shared by lexical and vector indexes."""
from __future__ import annotations

CHUNK_SIZE = 2000
CHUNK_OVERLAP = 0
QDRANT_CHUNKING_VERSION = "recursive_character_v1"
QDRANT_CHUNK_SEPARATORS = ("\n\n", "\n", " ", "")
