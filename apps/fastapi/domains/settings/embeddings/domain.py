"""embeddings — pure helpers (no I/O, no network, deterministic)."""
from __future__ import annotations


def normalize_base_url(url: str) -> str:
    """Return an OpenAI SDK base_url (scheme+host+prefix without /embeddings).

    Accepts:
      - http://host:8000/v1
      - http://host:8000/v1/embeddings  → strips trailing segment
      - http://host:8000/               → appends /v1
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    for suffix in ("/embeddings", "/embeddings/"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    if u.endswith("/v1"):
        return u
    if "://" in u and u.count("/") == 2:  # e.g. http://host:8000
        return u + "/v1"
    return u
