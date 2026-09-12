from __future__ import annotations

from .keys import _NON_CHAT_MARKERS


def classify_error(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "429" in msg or "rate limit" in msg:
        return "rate_limit"
    if "timeout" in name or "timed out" in msg:
        return "timeout"
    if "auth" in name or "401" in msg or "403" in msg or "invalid api key" in msg:
        return "auth_error"
    if "content" in name and "filter" in name:
        return "content_filter"
    if "5" in msg and ("server" in msg or "internal" in msg or "bad gateway" in msg):
        return "server_error"
    return "unknown"


def is_non_chat_model(model_id: str) -> bool:
    name = (model_id or "").lower()
    return any(m in name for m in _NON_CHAT_MARKERS)
