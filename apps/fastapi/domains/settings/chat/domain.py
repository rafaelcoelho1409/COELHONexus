"""chat — pure helpers (no I/O, no network, deterministic)."""
from __future__ import annotations

import re

import httpx


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


def rotator_health_url(base_url: str) -> str:
    """Rotator routing-health URL derived from the OpenAI-compat base_url
    (`<root>/api/v1/llm/openai/v1` → `<root>/api/v1/llm/routing/health`).
    "" when the endpoint isn't a COELHO LLM Rotator."""
    marker = "/llm/openai/"
    u = (base_url or "").strip().rstrip("/")
    i = u.find(marker)
    if i < 0:
        return ""
    return u[: i] + "/llm/routing/health"


def rotator_wall_extra(base_url: str, timeout_s: float | None, margin_s: float, min_s: float) -> dict:
    """`extra_body` carrying the per-request cascade budget for a COELHO LLM Rotator
    endpoint (`max_wall_s`, see params.ROTATOR_WALL_MARGIN_S); {} for any other
    OpenAI-compatible endpoint (an unknown body field would be rejected there) or when
    the caller set no timeout."""
    if not timeout_s or timeout_s <= 0 or not rotator_health_url(base_url):
        return {}
    return {"max_wall_s": round(max(float(timeout_s) - margin_s, min_s), 1)}


def pool_recovered(cooling: int, pool: int, max_frac: float) -> bool:
    """True when at most `max_frac` of the pool's arms are in cooldown. An
    unknown/empty pool (pool <= 0) is never "recovered" — keep waiting."""
    return pool > 0 and cooling <= max_frac * pool


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


_CAPACITY_MARKERS = ("pool exhausted", "pool_exhausted", "no deployments available", "no healthy deployments")


def capacity_wait_s(exc: BaseException, *, default_s: float, min_s: float, cap_s: float) -> float | None:
    """Seconds to wait before retrying when the Rotator has no usable arm right now (429 pool_exhausted, litellm's "No deployments available" 500); None for any other
    error. Honors the Retry-After header, else the "try again in N seconds" text, else `default_s`; clipped to [min_s, cap_s]."""
    text = f"{type(exc).__name__} {exc}".lower()
    if not any(m in text for m in _CAPACITY_MARKERS):
        return None
    wait = None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        if headers is not None and headers.get("retry-after") is not None:
            wait = float(headers.get("retry-after"))
    except (TypeError, ValueError):
        wait = None
    if wait is None:
        m = re.search(r"try again in ([0-9.]+) second", text)
        wait = float(m.group(1)) if m else default_s
    return max(min_s, min(cap_s, wait))


def is_length_error(exc: Exception) -> bool:
    """True when the model's output was cut at max_tokens (OpenAI SDK LengthFinishReasonError,
    wrapped by chat_structured_async into ChatError) — the budget was wrong, not the input."""
    text = f"{type(exc).__name__} {exc}"
    return "LengthFinishReason" in text or "length limit was reached" in text


def build_pool_limits(
    *,
    max_connections: int,
    max_keepalive_connections: int,
    keepalive_expiry_s: float,
) -> httpx.Limits:
    """Shared httpx pool shape — pure value-object construction (takes
    params as explicit args so it stays testable without params coupling)."""
    return httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
        keepalive_expiry=keepalive_expiry_s,
    )


def build_client_timeout(
    timeout_s: float | None,
    *,
    default_s: float,
    connect_s: float,
    write_s: float,
    pool_s: float,
) -> httpx.Timeout:
    """Per-request httpx timeout — the read ceiling is the call's own
    timeout (falls back to default_s); connect/write/pool are fixed."""
    t = timeout_s or default_s
    return httpx.Timeout(timeout=t, connect=connect_s, read=t, write=write_s, pool=pool_s)
