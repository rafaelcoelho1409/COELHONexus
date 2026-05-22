"""Per-run progress reporter + per-framework single-flight lock + cancel flag.

All three concerns live here because they all touch Redis from both the
FastAPI request handler and the Celery worker, and bundling them keeps the
ingestion package focused (one Redis-aware module instead of three).

Key layout:
    dd:runs:{run_id}:progress       -- JSON snapshot, <=1/s write throttle
    dd:runs:{run_id}:url_records    -- list, RPUSH per-fetch (no throttle)
    dd:runs:{run_id}:post           -- JSON dict, post-process summary
    dd:runs:{run_id}:cancel         -- "1" set by user-triggered cancel
    dd:lock:{framework_slug}        -- single-flight lock; value = active run_id

All TTLs are 2h except the lock (35 min -- slightly longer than the
Celery task's soft_time_limit of 30 min, so a crashed task still releases
the lock automatically before a manual override is needed).
"""
import json
import logging
import os
import time
from typing import Optional

import redis.asyncio as redis_aio

from .constants import (
    _CANCEL_POLL_THROTTLE_S,
    _LOCK_TTL_S,
    _RELEASE_SCRIPT,
    _THROTTLE_S,
    _TTL_S,
)
from .types import IngestCancelled


logger = logging.getLogger(__name__)


def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "redis-master.redis.svc.cluster.local")
    port = os.environ.get("REDIS_PORT", "6379")
    pwd = os.environ.get("REDIS_PASSWORD", "")
    return f"redis://:{pwd}@{host}:{port}" if pwd else f"redis://{host}:{port}"


def _progress_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:progress"


def _url_records_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:url_records"


def _post_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:post"


def _cancel_key(run_id: str) -> str:
    return f"dd:runs:{run_id}:cancel"


def _lock_key(framework_slug: str) -> str:
    return f"dd:lock:{framework_slug}"


# =============================================================================
# Single-flight lock -- per framework_slug, value = run_id holding it
# =============================================================================
async def acquire_lock(
    r: redis_aio.Redis, framework_slug: str, run_id: str,
    ttl_s: int = _LOCK_TTL_S,
) -> bool:
    """SETNX with TTL. Returns True if this run_id now holds the lock."""
    try:
        ok = await r.set(
            _lock_key(framework_slug), run_id, nx=True, ex=ttl_s,
        )
    except Exception as e:
        logger.warning(f"[lock] acquire failed: {e}")
        return False
    return bool(ok)


async def read_lock(
    r: redis_aio.Redis, framework_slug: str,
) -> Optional[str]:
    """Return the run_id currently holding the lock, or None."""
    try:
        v = await r.get(_lock_key(framework_slug))
    except Exception:
        return None
    if not v:
        return None
    return v.decode() if isinstance(v, bytes) else v


async def release_lock(
    r: redis_aio.Redis, framework_slug: str, run_id: str,
) -> bool:
    """Release the lock iff this run_id is the holder."""
    try:
        n = await r.eval(_RELEASE_SCRIPT, 1, _lock_key(framework_slug), run_id)
    except Exception as e:
        logger.warning(f"[lock] release failed: {e}")
        return False
    return bool(n)


# =============================================================================
# Cancel flag -- per run_id, set by the user via POST /runs/{run_id}/cancel
# =============================================================================
async def request_cancel(r: redis_aio.Redis, run_id: str) -> None:
    try:
        await r.set(_cancel_key(run_id), "1", ex=_TTL_S)
    except Exception as e:
        logger.warning(f"[cancel] set failed: {e}")


async def is_cancelled(r: redis_aio.Redis, run_id: str) -> bool:
    try:
        v = await r.get(_cancel_key(run_id))
    except Exception:
        return False
    return bool(v)


async def clear_cancel(r: redis_aio.Redis, run_id: str) -> None:
    try:
        await r.delete(_cancel_key(run_id))
    except Exception:
        pass


# =============================================================================
# Progress writer -- one instance per run, lazy-init Redis, throttled flushes
# =============================================================================
class Progress:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self._r: Optional[redis_aio.Redis] = None
        self._last_flush = 0.0
        self._last_cancel_check = 0.0
        self._state: dict = {
            "phase": "ingest",
            "tier": None,
            "current": 0,
            "total": 0,
            "last_url": "",
            "status": "idle",
            "updated_at": time.time(),
        }

    async def _client(self) -> Optional[redis_aio.Redis]:
        if self._r is None:
            try:
                self._r = redis_aio.from_url(
                    _redis_url(),
                    socket_connect_timeout=3.0,
                    socket_timeout=5.0,
                )
            except Exception as e:
                logger.warning(f"[progress] Redis init failed: {e}")
                return None
        return self._r

    async def _flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_flush) < _THROTTLE_S:
            return
        self._last_flush = now
        self._state["updated_at"] = now
        r = await self._client()
        if r is None:
            return
        try:
            await r.set(
                _progress_key(self.run_id),
                json.dumps(self._state),
                ex=_TTL_S,
            )
        except Exception as e:
            logger.info(f"[progress] write skipped: {e}")

    async def start(self, tier: str, total: int) -> None:
        self._state.update(
            tier=tier,
            total=max(0, int(total)),
            current=0,
            last_url="",
            status="running",
        )
        await self._flush(force=True)

    async def update_total(self, total: int) -> None:
        self._state["total"] = max(0, int(total))
        await self._flush(force=True)

    async def update(self, current: int, last_url: str = "") -> None:
        self._state.update(
            current=max(0, int(current)),
            last_url=(last_url or "")[:200],
        )
        await self._flush(force=False)

    async def check_cancelled(self) -> bool:
        """Poll the cancel flag (throttled to <=1/s). Returns True when the
        user has clicked Cancel. Tier modules should call this between
        URL fetches; on True they raise IngestCancelled to trigger the
        dispatcher's cleanup path."""
        now = time.time()
        if (now - self._last_cancel_check) < _CANCEL_POLL_THROTTLE_S:
            return False
        self._last_cancel_check = now
        r = await self._client()
        if r is None:
            return False
        return await is_cancelled(r, self.run_id)

    async def raise_if_cancelled(self) -> None:
        if await self.check_cancelled():
            raise IngestCancelled(self.run_id)

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
        r = await self._client()
        if r is None:
            return
        rec = {
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
            pipe = r.pipeline()
            pipe.rpush(_url_records_key(self.run_id), json.dumps(rec))
            pipe.expire(_url_records_key(self.run_id), _TTL_S)
            await pipe.execute()
        except Exception as e:
            logger.info(f"[progress] record_url skipped: {e}")

    async def record_post(
        self,
        *,
        tier: Optional[str] = None,
        input_files: int,
        input_bytes: int,
        output_files: int,
        output_bytes: int,
        was_split: bool,
        stubs_dropped: int = 0,
        duplicates_dropped: int = 0,
        notes: Optional[str] = None,
    ) -> None:
        r = await self._client()
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
            "stubs_dropped": int(stubs_dropped),
            "duplicates_dropped": int(duplicates_dropped),
            "notes": (notes or "")[:200] if notes else None,
            "recorded_at": time.time(),
        }
        try:
            await r.set(_post_key(self.run_id), json.dumps(payload), ex=_TTL_S)
        except Exception as e:
            logger.info(f"[progress] record_post skipped: {e}")

    async def finish(self, status: str = "done") -> None:
        """`status` in done / failed / aborted / downgrade / cancelled."""
        self._state["status"] = status
        await self._flush(force=True)

    async def close(self) -> None:
        if self._r is not None:
            try:
                await self._r.aclose()
            except Exception:
                pass
            self._r = None


# =============================================================================
# Read-side helpers (consumed by the runs router / SSE pump)
# =============================================================================
async def read_progress(r: redis_aio.Redis, run_id: str) -> Optional[dict]:
    try:
        raw = await r.get(_progress_key(run_id))
    except Exception:
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        return None


async def read_url_records(r: redis_aio.Redis, run_id: str) -> list[dict]:
    try:
        raw_list = await r.lrange(_url_records_key(run_id), 0, -1)
    except Exception:
        return []
    out: list[dict] = []
    for raw in raw_list or []:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


async def read_post(r: redis_aio.Redis, run_id: str) -> Optional[dict]:
    try:
        raw = await r.get(_post_key(run_id))
    except Exception:
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        return None
