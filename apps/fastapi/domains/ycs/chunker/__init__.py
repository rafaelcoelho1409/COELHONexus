"""ycs/chunker — pure RecursiveCharacterTextSplitter wrapper.

Direct port of deprecated `services/youtube/chunker.py`."""
from .domain import chunk_transcript, create_chunker
from .params import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS, SEPARATORS


__all__ = [
    "CHUNK_OVERLAP_CHARS",
    "CHUNK_SIZE_CHARS",
    "SEPARATORS",
    "chunk_transcript",
    "create_chunker",
]
