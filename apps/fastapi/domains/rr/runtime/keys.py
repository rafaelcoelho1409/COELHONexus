"""Redis URL + channel/key builders for RR runtime events.

Per docs/CODE-CONVENTIONS.md §2: identifier-style values (Redis channel
names, key namespaces) live in keys.py; numeric tunables (timeouts,
retention) live in params.py.
"""
from __future__ import annotations

import os
from urllib.parse import quote


def redis_url() -> str:
    """Build the Redis URL from env. Strict reads — secrets must be set.
    Same pattern as `domains/dd/planner/keys.py::redis_url()`."""
    host = os.environ["REDIS_HOST"]
    port = os.environ["REDIS_PORT"]
    password = os.environ["REDIS_PASSWORD"]
    if password:
        return f"redis://:{quote(password, safe='')}@{host}:{port}"
    return f"redis://{host}:{port}"


def event_channel(scan_id: str) -> str:
    """Redis pub/sub channel for live phase events of one scan.
    SSE clients subscribe here; the Celery task publishes here."""
    return f"rr:{scan_id}:events"


def snapshot_key(scan_id: str) -> str:
    """Redis LIST key for the TTL'd event replay buffer. A late SSE
    subscriber replays history before live events to catch up."""
    return f"rr:{scan_id}:snapshot"
