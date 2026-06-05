"""Per-run progress reporter + per-framework single-flight lock + cancel flag."""
from __future__ import annotations

from .errors import IngestCancelled
from .keys import redis_url
from .service import (
    Progress,
    acquire_lock,
    clear_cancel,
    is_cancelled,
    read_lock,
    read_post,
    read_progress,
    read_url_records,
    release_lock,
    request_cancel,
)

__all__ = [
    "IngestCancelled",
    "Progress",
    "acquire_lock",
    "clear_cancel",
    "is_cancelled",
    "read_lock",
    "read_post",
    "read_progress",
    "read_url_records",
    "redis_url",
    "release_lock",
    "request_cancel",
]
