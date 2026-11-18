"""Stable identifiers for Elasticsearch transcript passage documents."""
from __future__ import annotations

import hashlib


def passage_document_id(transcript_id: str, chunk_index: int, content: str) -> str:
    identity = f"coelhonexus:ycs:passage:{transcript_id}:{chunk_index}:{content}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
