"""Server-side library/stats fetches with a per-key TTL cache.

Without the cache, every `/` request would issue blocking httpx calls →
under load (heavy bandit cascades during synth/planner runs), each call
holds a Starlette threadpool worker for up to 3s, surfacing as "page
keeps loading". Cache survives 60s; on backend failure we serve the
last known value rather than empty.

2026-06-18 SOTA-homepage refactor: home stats now span all three
products. `fetch_library()` (DD ingestion library) is joined by
`fetch_ycs_total()` (videos processed) and `fetch_rr_total()` (scans
launched), so the hero stats strip reflects DD + YCS + RR — not just
DD as it did pre-redesign.

2026-06-18 second pass — first-paint stats fix:
  Problem: `/api/v1/docs-distiller/ingestion` runs ~3.0-3.3s end-to-end
  (MinIO bucket walk + Postgres join). The previous `timeout = 2.5`
  was below that floor, so EVERY cold call raised httpx.ReadTimeout
  → `_cached` returned None → both DD stats ("Frameworks ingested",
  "Corpus size") rendered as `—` on first visit, then flickered to
  numbers on a refresh once the backend had warmed.

  Fix:
    1. timeout bumped 2.5s → 5.0s (covers DD floor with buffer).
    2. Background pre-warm: a daemon thread fires all three fetches
       at module import, concurrently, so the cache is populated by
       the time the first user request lands on `/`. The thread is a
       no-op on failure (e.g., FastAPI sidecar not ready yet at boot);
       the request-time call will repopulate the cache on its own."""
import threading
import time

import httpx

from proxy import FASTAPI_URL


_TTL_S    = 60.0
_TIMEOUT  = 5.0       # was 2.5 — below DD ingestion's ~3s floor
_cache: dict[str, dict] = {}


def _cached(key: str, fetcher):
    """Generic 60s TTL wrapper. On exception, return last known value
    if any, else None."""
    now = time.monotonic()
    entry = _cache.get(key)
    if entry and (now - entry["ts"]) < _TTL_S:
        return entry["data"]
    try:
        data = fetcher()
        _cache[key] = {"data": data, "ts": now}
        return data
    except Exception:
        return entry["data"] if entry else None


def fetch_library() -> list[dict]:
    """DD ingestion library. Empty list on cold-start failure."""
    def _go():
        r = httpx.get(
            f"{FASTAPI_URL}/api/v1/docs-distiller/ingestion", timeout = _TIMEOUT,
        )
        r.raise_for_status()
        return r.json() or []
    return _cached("dd_library", _go) or []


def fetch_ycs_total() -> int | None:
    """YCS processed-video count. None on cold-start failure (renders as —).

    Note: the backend API prefix is `/api/v1/ycs/admin/...` — distinct from
    the FastHTML user-facing route `/youtube-content-search/...`. The
    earlier revision used the user-facing prefix and hit 404."""
    def _go():
        r = httpx.get(
            f"{FASTAPI_URL}/api/v1/ycs/admin/videos",
            params  = {"limit": 1, "offset": 0},
            timeout = _TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json() or {}
        return int(payload.get("total") or 0)
    return _cached("ycs_total", _go)


def fetch_rr_total() -> int | None:
    """RR launched-scans count (last 100). None on cold-start failure."""
    def _go():
        r = httpx.get(
            f"{FASTAPI_URL}/api/v1/rr/scans/recent",
            params  = {"limit": 100, "profile_id": "default"},
            timeout = _TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json() or {}
        return len(payload.get("items") or [])
    return _cached("rr_total", _go)


def _prewarm() -> None:
    """Fire all three fetches concurrently in daemon threads at import.

    Runs once at module load — i.e., at FastHTML app boot. Each thread
    calls one fetcher; failures are silently swallowed by `_cached`.
    By the time the first user request reaches `/`, all three caches
    are populated (3-second worst-case race with the user), so the
    stats strip renders real numbers on first paint instead of dashes.

    If the FastAPI sidecar isn't reachable at boot (cold cluster),
    the warmup is a no-op and the first request will trigger the
    real fetches with the bumped 5s timeout."""
    for fn in (fetch_library, fetch_ycs_total, fetch_rr_total):
        threading.Thread(target = fn, daemon = True).start()


_prewarm()
