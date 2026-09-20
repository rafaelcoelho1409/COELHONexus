"""Redis URL + channel/key builders for RR runtime — events, extraction
cache, fs mirror.

Per docs/CODE-CONVENTIONS.md §2: identifier-style values (Redis channel
names, key namespaces) live in keys.py; numeric tunables (timeouts,
retention) live in params.py.
"""
from __future__ import annotations

import os
from urllib.parse import quote

from . import params


def redis_url() -> str:
    """Build the Redis URL from env. Strict reads — secrets must be set.
    Same pattern as `domains/dd/planner/keys.py::redis_url()`."""
    host = os.environ["REDIS_HOST"]
    port = os.environ["REDIS_PORT"]
    password = os.environ["REDIS_PASSWORD"]
    if password:
        return f"redis://:{quote(password, safe='')}@{host}:{port}"
    return f"redis://{host}:{port}"


# Events — SSE pub/sub + task-id store
def event_channel(scan_id: str) -> str:
    """Redis pub/sub channel for live phase events of one scan.
    SSE clients subscribe here; the Celery task publishes here."""
    return f"rr:{scan_id}:events"


def snapshot_key(scan_id: str) -> str:
    """Redis LIST key for the TTL'd event replay buffer. A late SSE
    subscriber replays history before live events to catch up."""
    return f"rr:{scan_id}:snapshot"


def task_id_key(scan_id: str) -> str:
    """Redis STRING key holding the Celery task UUID for an active scan,
    written by `POST /scan` and read by `POST /scan/{id}/cancel` to
    issue `AsyncResult.revoke(terminate=True)` against the right task."""
    return f"rr:{scan_id}:task_id"


# Extraction cache — content-addressed by (prompt_version, arxiv_id)
def extraction_cache_key(arxiv_id: str) -> str:
    """Redis key — prompt-version namespaced so cache survives a
    prompt-version bump (old keys expire via TTL)."""
    aid = (arxiv_id or "").strip()
    if not aid:
        raise ValueError("arxiv_id is required for extraction cache")
    return f"rr:cache:extraction:{params.EXTRACTION_PROMPT_VERSION}:{aid}"


# fs mirror — Redis read-side mirror of the agent's per-scan virtual fs
def fs_mirror_key(scan_id: str, path: str) -> str:
    """Redis key for one fs entry. Path collisions are impossible since
    the agent's fs paths are namespaced (discovery/, extractions/, …)."""
    return f"rr:{scan_id}:fs:{path}"


def fs_mirror_index_key(scan_id: str) -> str:
    """Redis SET key tracking every path written for this scan, so the
    drawer can ask "what's in fs?" without scanning Redis."""
    return f"rr:{scan_id}:fs:index"


# Code synth status — tracks an in-flight/failed Build-tab generation so
# the Digest page shows "pending" across a page refresh instead of a
# false idle/404 while the Celery task is still running.
def code_synth_status_key(scan_id: str, arxiv_id: str, prompt_version: str) -> str:
    """Redis STRING key (JSON body) — absent means "never started" or
    "already resolved" (the MinIO cache is the source of truth once
    done; this key is cleared on success, not flipped to "done")."""
    return f"rr:{scan_id}:code_synth:{prompt_version}:{arxiv_id}:status"
