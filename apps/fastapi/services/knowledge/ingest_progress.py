"""
Knowledge Distiller — Ingestion Progress Reporter

Writes throttled ingestion progress events to Redis so the `/studies/{id}/stream`
SSE endpoint can surface per-page progress during long crawls (Tier 2/3 = many
minutes; Tier 4 = up to 20 min). Before this, the user saw one `phase=ingest`
event and then nothing until the entire ingest superstep finished.

DESIGN

  - Per-study instance `IngestProgress(study_id)`. The tier function creates
    one at the top, calls `.start(tier_name, total)` once the URL set is
    known, `.update(n, url)` per page (throttled), `.finish(status)` at end.
  - All writes go to Redis key
      `coelhonexus:knowledge:ingest_progress:{study_id}`
    with TTL = 1 hour (plenty for any single ingest run).
  - Throttled: writes coalesced to ≤1/second. Per-page updates from a
    Semaphore(10) fetch loop could fire 10× per second otherwise.
  - No-op when study_id is None — legacy callers (before Step 8 wiring)
    use IngestProgress harmlessly.
  - Own Redis connection (built from `REDIS_URL` env). The Celery worker
    doesn't share FastAPI's `app.state.redis_aio`, so we build our own
    cheap client. Closed via `.close()` on tier function exit.

CONSUMED BY

  `routers/v1/knowledge/distiller.py::stream_study` polls this Redis key
  every ~1s alongside the Celery AsyncResult meta, merges both into the
  SSE event stream.
"""
import json
import logging
import os
import time
from typing import Optional


logger = logging.getLogger(__name__)


_KEY_PREFIX = "coelhonexus:knowledge:ingest_progress:"
_KEY_TTL_S = 3600
_THROTTLE_S = 1.0


class IngestProgress:
    """
    Per-study ingestion progress reporter. Throttled Redis writer —
    no-op when study_id is None (legacy callers stay unaffected).

    Usage:
        progress = IngestProgress(cfg.study_id)
        try:
            await progress.start(tier = "sitemap", total = len(urls))
            for i, url in enumerate(urls, 1):
                ... fetch ...
                await progress.update(i, url)
            await progress.finish(status = "done")
        finally:
            await progress.close()
    """

    def __init__(self, study_id: Optional[str]):
        self.study_id = study_id
        self._redis = None       # lazy-initialized; False sentinel means "gave up"
        self._last_flush: float = 0.0
        self._state: dict = {
            "phase": "ingest",
            "tier": None,
            "current": 0,
            "total": 0,
            "last_url": "",
            "status": "idle",
            "updated_at": time.time(),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _key(self) -> str:
        return f"{_KEY_PREFIX}{self.study_id}"

    async def _get_redis(self):
        """Lazy redis.asyncio client. Returns None on persistent failure."""
        if self.study_id is None:
            return None
        if self._redis is False:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as redis_aio
                # Mirror app.py's URL-build pattern — the pods expose
                # REDIS_HOST / REDIS_PORT / REDIS_PASSWORD as discrete env
                # vars (from the helm values secret bindings), not a
                # pre-composed REDIS_URL. Auth is required on the cluster
                # Redis instance.
                host = os.environ.get("REDIS_HOST", "redis-master.redis.svc.cluster.local")
                port = os.environ.get("REDIS_PORT", "6379")
                password = os.environ.get("REDIS_PASSWORD", "")
                if password:
                    url = f"redis://:{password}@{host}:{port}"
                else:
                    url = f"redis://{host}:{port}"
                # Reduce connect timeout so a dead Redis doesn't hang ingest.
                self._redis = redis_aio.from_url(
                    url,
                    socket_connect_timeout = 3.0,
                    socket_timeout = 5.0,
                )
            except Exception as e:
                logger.warning(f"[ingest-progress] Redis init failed: {e}")
                self._redis = False
                return None
        return self._redis

    async def _flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_flush) < _THROTTLE_S:
            return
        self._last_flush = now
        self._state["updated_at"] = now
        r = await self._get_redis()
        if r is None:
            return
        try:
            await r.set(self._key(), json.dumps(self._state), ex = _KEY_TTL_S)
        except Exception as e:
            # Don't fail ingestion on a Redis hiccup — just skip this flush.
            logger.info(f"[ingest-progress] Redis write skipped: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def start(self, tier: str, total: int) -> None:
        """
        Begin a tier run. `tier` is the manifest tier name
        ("llms_full_txt" / "llms_txt" / "sitemap" / "crawl4ai" /
        "github_readme_only"). `total` is the expected page count.
        Flushed immediately so the first /stream event appears right away.
        """
        self._state.update(
            tier = tier,
            total = max(0, int(total)),
            current = 0,
            last_url = "",
            status = "running",
        )
        await self._flush(force = True)

    async def update(self, current: int, last_url: str = "") -> None:
        """
        Report progress after a page succeeded. Throttled to ≤1/s so a
        10-concurrent fetch loop doesn't hammer Redis.
        """
        self._state.update(
            current = max(0, int(current)),
            last_url = (last_url or "")[:200],
        )
        await self._flush(force = False)

    async def finish(self, status: str = "done") -> None:
        """
        Mark tier complete. `status` is one of: "done", "failed", "aborted".
        Always force-flushed so the final state is visible to /stream.
        """
        self._state["status"] = status
        await self._flush(force = True)

    async def close(self) -> None:
        """Close the lazy Redis client. Safe to call multiple times."""
        if self._redis and self._redis is not False:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        self._redis = False  # mark as closed


# =============================================================================
# Read-side helper (consumed by /studies/{id}/stream)
# =============================================================================
async def read_progress(redis_aio, study_id: str) -> Optional[dict]:
    """
    Fetch the latest ingest progress snapshot for a study. Returns None if
    no progress has been written (yet) or Redis is unavailable.

    Called from the SSE `/stream` event loop — safe to call every ~1s.
    """
    if not study_id:
        return None
    try:
        raw = await redis_aio.get(f"{_KEY_PREFIX}{study_id}")
    except Exception as e:
        logger.info(f"[ingest-progress] read failed: {e}")
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        return None
