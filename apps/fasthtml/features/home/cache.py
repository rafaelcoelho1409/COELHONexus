"""Server-side library fetch with TTL cache.

Without the cache, every `/` request issued a fresh blocking httpx call →
under load (heavy bandit cascades during synth/planner runs), the call held
a Starlette threadpool worker for up to 3s, surfacing as "page keeps
loading". Cache survives 60s; on backend failure we serve the last known
library rather than empty."""
import time

import httpx

from proxy import FASTAPI_URL


_LIBRARY_TTL_S = 60.0
_library_cache: dict = {"data": None, "ts": 0.0}


def fetch_library() -> list[dict]:
    """Returns the cached library list. Empty list on cold-start failure."""
    now = time.monotonic()
    if (
        _library_cache["data"] is not None
        and (now - _library_cache["ts"]) < _LIBRARY_TTL_S
    ):
        return _library_cache["data"]
    try:
        r = httpx.get(
            f"{FASTAPI_URL}/api/v1/docs-distiller/ingestion", timeout = 2.5,
        )
        r.raise_for_status()
        data = r.json() or []
        _library_cache["data"] = data
        _library_cache["ts"] = now
        return data
    except Exception:
        return _library_cache["data"] or []
