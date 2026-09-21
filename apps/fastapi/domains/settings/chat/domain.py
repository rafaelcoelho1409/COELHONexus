"""chat — pure helpers (no I/O, no network, deterministic)."""
from __future__ import annotations


def normalize_base_url(url: str) -> str:
    """Return an OpenAI SDK base_url (scheme+host+prefix without /chat/completions).

    Accepts:
      - http://host:8000/v1
      - http://host:8000/v1/chat/completions  → strips trailing segment
      - http://host:8000/                     → appends /v1
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    for suffix in ("/chat/completions", "/chat/completions/"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    if u.endswith("/v1"):
        return u
    if "://" in u and u.count("/") == 2:  # e.g. http://host:8000
        return u + "/v1"
    return u


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
