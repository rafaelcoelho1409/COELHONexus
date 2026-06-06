"""ycs/ingestion — Qdrant point-id builder.

Per `docs/CODE-CONVENTIONS.md` §2, key-builder functions belong in
`keys.py`. The md5 hash makes the id deterministic across re-ingests
(Qdrant upsert is idempotent on identical ids)."""
from __future__ import annotations

import hashlib


def point_id(video_id: str, chunk_index: int) -> str:
    """Deterministic Qdrant point id — re-ingesting the same chunk
    overwrites the existing point in place. Mirror of deprecated
    `_deterministic_id` (`ingestion.py:L49-52`)."""
    raw = f"{video_id}_{chunk_index}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()
