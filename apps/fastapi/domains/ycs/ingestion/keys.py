"""ycs/ingestion — Qdrant point-id builder + streaming-buffer Redis keys.

Per `docs/CODE-CONVENTIONS.md` §2, key-builder functions belong in
`keys.py`. The md5 hash makes the id deterministic across re-ingests
(Qdrant upsert is idempotent on identical ids).

2026-09-13: `qdrant_buffer_key`/`qdrant_flush_lock_key` back
`streaming.py`'s Redis-shared chunk buffer. These deliberately use a
locally-owned prefix (`STREAMING_KEY_PREFIX` in `params.py`) instead of
importing `pipeline_task.params.PIPELINE_STATE_PREFIX` — that import
would pull in `pipeline_task/__init__.py`'s full chain (`.task` →
every `domains.ycs.*` Celery task module) just to read one string
constant, which is exactly the "clean import in test environments"
problem `pipeline_task/service.py`'s own docstring already calls out.
The two prefixes share the same literal value (`"ycs:pipeline:"`) by
convention, not by import — if one changes, update the other."""
from __future__ import annotations

import hashlib

from .params import STREAMING_KEY_PREFIX


def point_id(video_id: str, chunk_index: int) -> str:
    """Deterministic Qdrant point id — re-ingesting the same chunk
    overwrites the existing point in place.py:L49-52`)."""
    raw = f"{video_id}_{chunk_index}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def qdrant_buffer_key(extract_id: str) -> str:
    """Redis LIST of pending chunk JSON blobs, shared across every
    video's `stream_video_to_qdrant` task for one run — the streaming
    replacement for the bulk path's in-memory cross-video buffer
    (which only works within one task's lifetime)."""
    return f"{STREAMING_KEY_PREFIX}{extract_id}:qdrant:buffer"


def qdrant_flush_lock_key(extract_id: str) -> str:
    """Guards the buffer-drain critical section so two videos finishing
    at nearly the same moment don't both pop + upsert the same
    chunks."""
    return f"{STREAMING_KEY_PREFIX}{extract_id}:qdrant:flush_lock"
