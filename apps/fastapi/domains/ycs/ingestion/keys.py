"""ycs/ingestion — Qdrant point-id builder + streaming-buffer Redis keys.

Per `docs/CODE-CONVENTIONS.md` §2, key-builder functions belong in
`keys.py`. The md5 hash makes the id deterministic across re-ingests
(Qdrant upsert is idempotent on identical ids).

2026-09-13: `qdrant_buffer_key`/`qdrant_flush_lock_key` back
`service.py`'s Redis-shared chunk buffer. These deliberately use a
locally-owned prefix (`params.STREAMING_KEY_PREFIX` in `params.py`) instead of
importing `pipeline_task.params.PIPELINE_STATE_PREFIX` — that import
would pull in `pipeline_task/__init__.py`'s full chain (`.task` →
every `domains.ycs.*` Celery task module) just to read one string
constant, which is exactly the "clean import in test environments"
problem `pipeline_task/service.py`'s own docstring already calls out.
The two prefixes share the same literal value (`"ycs:pipeline:"`) by
convention, not by import — if one changes, update the other."""
from __future__ import annotations

import hashlib

from . import params


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
    return f"{params.STREAMING_KEY_PREFIX}{extract_id}:qdrant:buffer"


def qdrant_flush_lock_key(extract_id: str) -> str:
    """Guards the buffer-drain critical section so two videos finishing
    at nearly the same moment don't both pop + upsert the same
    chunks."""
    return f"{params.STREAMING_KEY_PREFIX}{extract_id}:qdrant:flush_lock"


def qdrant_draining_key(extract_id: str) -> str:
    """2026-09-14: set while a buffer flush (periodic OR the final
    drain) is actively embedding+upserting, cleared when it returns —
    with a short TTL as a self-healing backstop if the worker process
    is hard-killed mid-flush (a SIGTERM revoke doesn't run Python
    cleanup code, so an un-cleared flag would otherwise wedge the bar
    at PROGRESS forever).

    `pipeline_task.service.get_phase_progress` checks this for the
    "qdrant" phase before reporting SUCCESS. Without it: the per-video
    finished counter reaches its total the INSTANT the last video's
    `mark_video_done` call returns — which is BEFORE the drain even
    starts, let alone finishes. The bar showed "Done" while a slow
    embedding call was still in flight, with no signal that stopping
    the run would kill it mid-write — exactly what let a Stop click
    (aimed at a different, unrelated phase) silently kill an in-flight
    drain the user had no way to know was still running."""
    return f"{params.STREAMING_KEY_PREFIX}{extract_id}:qdrant:draining"
