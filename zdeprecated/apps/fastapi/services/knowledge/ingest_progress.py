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
_URL_LIST_PREFIX = "coelhonexus:knowledge:ingest_urls:"
_POST_INGEST_PREFIX = "coelhonexus:knowledge:ingest_post:"
_KEY_TTL_S = 3600
_URL_LIST_TTL_S = 7200
_POST_INGEST_TTL_S = 7200
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

    def _url_list_key(self) -> str:
        return f"{_URL_LIST_PREFIX}{self.study_id}"

    def _post_ingest_key(self) -> str:
        return f"{_POST_INGEST_PREFIX}{self.study_id}"

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

    async def record_url(
        self,
        url: str,
        *,
        status: str,
        tier: Optional[str] = None,
        http_code: Optional[int] = None,
        fetch_ms: Optional[int] = None,
        bytes_fetched: Optional[int] = None,
        extracted_chars: Optional[int] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        """
        Append a per-URL record to the observability Redis list. Used by the
        FastHTML /kd/studies/{id}/observability/ingestion page to render the
        per-URL table in real time. Non-throttled — every fetch attempt
        emits one record.

        `status` values: "success" | "http_error" | "timeout" |
                         "extract_empty" | "fetch_error".

        No LTRIM — we keep every URL so the operator can audit large corpora
        (Docker had 1,341 URLs and we don't want to lose detail). At ~300 B
        per record + JSON overhead that's <1 MB for a 1,500-URL study; well
        within Redis budget.
        """
        if self.study_id is None:
            return
        r = await self._get_redis()
        if r is None:
            return
        record = {
            "url": (url or "")[:500],
            "tier": tier or self._state.get("tier"),
            "status": status,
            "http_code": http_code,
            "fetch_ms": fetch_ms,
            "bytes": bytes_fetched,
            "extracted_chars": extracted_chars,
            "error_msg": (error_msg or "")[:500] if error_msg else None,
            "recorded_at": time.time(),
        }
        try:
            key = self._url_list_key()
            pipe = r.pipeline()
            pipe.rpush(key, json.dumps(record))
            pipe.expire(key, _URL_LIST_TTL_S)
            await pipe.execute()
        except Exception as e:
            logger.info(f"[ingest-progress] record_url skipped: {e}")

    async def record_post_ingest_summary(
        self,
        *,
        tier: Optional[str] = None,
        input_files: int,
        input_bytes: int,
        output_files: int,
        output_bytes: int,
        was_split: bool,
        notes: Optional[str] = None,
    ) -> None:
        """
        Record the post-ingest normalization summary (currently just the
        monolith-split step in `post_ingest.split_monolith_if_needed`).

        Surfaces the multiplier between URLs fetched and files actually
        present in MinIO under `study_root/research/raw/`. On Tier 1 with
        a large `llms-full.txt`, input_files=1 → output_files can be 700+.
        On Tier 3/4 multi-page paths input_files == output_files and
        was_split=False.

        Written as a single JSON dict at
        `coelhonexus:knowledge:ingest_post:{study_id}`.
        """
        if self.study_id is None:
            return
        r = await self._get_redis()
        if r is None:
            return
        payload = {
            "tier": tier or self._state.get("tier"),
            "input_files": int(input_files),
            "input_bytes": int(input_bytes),
            "output_files": int(output_files),
            "output_bytes": int(output_bytes),
            "expansion_ratio": (
                float(output_files) / float(input_files)
                if input_files > 0 else 0.0
            ),
            "was_split": bool(was_split),
            "notes": (notes or "")[:200] if notes else None,
            "recorded_at": time.time(),
        }
        try:
            await r.set(
                self._post_ingest_key(),
                json.dumps(payload),
                ex = _POST_INGEST_TTL_S,
            )
        except Exception as e:
            logger.info(f"[ingest-progress] post_ingest skipped: {e}")

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


async def read_urls(redis_aio, study_id: str) -> list[dict]:
    """
    Fetch all per-URL records for a study (in fetch-completion order). Used
    by the observability page. Returns [] if no records or Redis unavailable.

    No pagination — operators need to see every URL on a large corpus
    (1,341 URLs at <1 MB total is well within a single response).
    """
    if not study_id:
        return []
    try:
        raw_list = await redis_aio.lrange(f"{_URL_LIST_PREFIX}{study_id}", 0, -1)
    except Exception as e:
        logger.info(f"[ingest-progress] read_urls failed: {e}")
        return []
    records: list[dict] = []
    for raw in raw_list or []:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            records.append(json.loads(raw))
        except Exception:
            continue
    return records


async def read_post_ingest(redis_aio, study_id: str) -> Optional[dict]:
    """
    Fetch the post-ingest normalization summary for a study, or None.
    """
    if not study_id:
        return None
    try:
        raw = await redis_aio.get(f"{_POST_INGEST_PREFIX}{study_id}")
    except Exception as e:
        logger.info(f"[ingest-progress] read_post_ingest failed: {e}")
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        return None
