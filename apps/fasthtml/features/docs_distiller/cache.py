"""Server-side framework-catalog fetch with TTL cache.

Without the cache, every /docs-distiller* GET issued a fresh blocking httpx
call to FastAPI. Under heavy bandit cascades the call could hold a
Starlette threadpool worker for up to 5s, surfacing as "page keeps
loading". Cache survives 60s; on backend failure we serve the last good
value so the picker keeps working through brief FastAPI hiccups."""
import time

import httpx

from proxy import FASTAPI_URL


_CATALOG_TTL_S = 60.0
_catalog_cache: dict = {"data": None, "ts": 0.0}


def fetch_catalog() -> list[dict]:
    now = time.monotonic()
    if (
        _catalog_cache["data"] is not None
        and (now - _catalog_cache["ts"]) < _CATALOG_TTL_S
    ):
        return _catalog_cache["data"]
    try:
        r = httpx.get(
            f"{FASTAPI_URL}/api/v1/docs-distiller/resolver", timeout = 2.5,
        )
        r.raise_for_status()
        data = r.json() or []
        _catalog_cache["data"] = data
        _catalog_cache["ts"] = now
        return data
    except Exception:
        return _catalog_cache["data"] or []
