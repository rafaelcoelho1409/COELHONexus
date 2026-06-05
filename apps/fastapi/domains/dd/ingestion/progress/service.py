"""Per-run progress + per-framework single-flight lock + cancel flag.

Keys:
    dd:runs:{run_id}:progress     JSON snapshot (≤1/s throttle)
    dd:runs:{run_id}:url_records  per-fetch list
    dd:runs:{run_id}:post         post-process summary
    dd:runs:{run_id}:cancel       cancel flag
    dd:lock:{framework_slug}      value = holding run_id
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import redis.asyncio as redis_aio

from .errors import IngestCancelled
from .keys import (
    cancel_key,
    lock_key,
    post_key,
    progress_key,
    redis_url,
    url_records_key,
)
from .params import (
    CANCEL_POLL_THROTTLE_S,
    LOCK_TTL_S,
    THROTTLE_S,
    TTL_S,
)


logger = logging.getLogger(__name__)


# Lua compare-and-delete — never release someone else's lock.
RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


async def acquire_lock(
    r: redis_aio.Redis,
    framework_slug: str,
    run_id: str,
    ttl_s: int = LOCK_TTL_S,
) -> bool:
    """SETNX with TTL. True if this run_id now holds the lock."""
    try:
        ok = await r.set(lock_key(framework_slug), run_id, nx=True, ex=ttl_s)
    except Exception as e:
        logger.warning(f"[lock] acquire failed: {e}")
        return False
    return bool(ok)


async def read_lock(r: redis_aio.Redis, framework_slug: str) -> Optional[str]:
    """run_id holding the lock, or None."""
    try:
        v = await r.get(lock_key(framework_slug))
    except Exception:
        return None
    if not v:
        return None
    return v.decode() if isinstance(v, bytes) else v


async def release_lock(
    r: redis_aio.Redis,
    framework_slug: str,
    run_id: str,
) -> bool:
    """Release iff this run_id is the holder."""
    try:
        n = await r.eval(RELEASE_SCRIPT, 1, lock_key(framework_slug), run_id)
    except Exception as e:
        logger.warning(f"[lock] release failed: {e}")
        return False
    return bool(n)


async def request_cancel(r: redis_aio.Redis, run_id: str) -> None:
    try:
        await r.set(cancel_key(run_id), "1", ex=TTL_S)
    except Exception as e:
        logger.warning(f"[cancel] set failed: {e}")


async def is_cancelled(r: redis_aio.Redis, run_id: str) -> bool:
    try:
        v = await r.get(cancel_key(run_id))
    except Exception:
        return False
    return bool(v)


async def clear_cancel(r: redis_aio.Redis, run_id: str) -> None:
    try:
        await r.delete(cancel_key(run_id))
    except Exception:
        pass


class Progress:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._r: Optional[redis_aio.Redis] = None
        self._last_flush = 0.0
        self._last_cancel_check = 0.0
        self._state: dict = {
            "phase":      "ingest",
            "tier":       None,
            "current":    0,
            "total":      0,
            "last_url":   "",
            "status":     "idle",
            "updated_at": time.time(),
        }

    async def _client(self) -> Optional[redis_aio.Redis]:
        if self._r is None:
            try:
                self._r = redis_aio.from_url(
                    redis_url(),
                    socket_connect_timeout = 3.0,
                    socket_timeout = 5.0,
                )
            except Exception as e:
                logger.warning(f"[progress] Redis init failed: {e}")
                return None
        return self._r

    async def _flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_flush) < THROTTLE_S:
            return
        self._last_flush = now
        self._state["updated_at"] = now
        r = await self._client()
        if r is None:
            return
        try:
            await r.set(
                progress_key(self.run_id), 
                json.dumps(self._state), 
                ex = TTL_S)
        except Exception as e:
            logger.info(f"[progress] write skipped: {e}")

    async def start(self, tier: str, total: int) -> None:
        self._state.update(
            tier = tier,
            total = max(0, int(total)),
            current = 0,
            last_url = "",
            status = "running",
        )
        await self._flush(force = True)

    async def update_total(self, total: int) -> None:
        self._state["total"] = max(0, int(total))
        await self._flush(force=True)

    async def update(self, current: int, last_url: str = "") -> None:
        self._state.update(
            current = max(0, int(current)),
            last_url = (last_url or "")[:200],
        )
        await self._flush(force = False)

    async def check_cancelled(self) -> bool:
        """≤1/s polling; tiers call between fetches → raise IngestCancelled."""
        now = time.time()
        if (now - self._last_cancel_check) < CANCEL_POLL_THROTTLE_S:
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
            "url":             (url or "")[:500],
            "tier":            tier or self._state.get("tier"),
            "status":          status,
            "http_code":       http_code,
            "fetch_ms":        fetch_ms,
            "bytes":           bytes_fetched,
            "extracted_chars": extracted_chars,
            "error_msg":       (error_msg or "")[:500] if error_msg else None,
            "recorded_at":     time.time(),
        }
        try:
            pipe = r.pipeline()
            pipe.rpush(url_records_key(self.run_id), json.dumps(rec))
            pipe.expire(url_records_key(self.run_id), TTL_S)
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
            "tier":               tier or self._state.get("tier"),
            "input_files":        int(input_files),
            "input_bytes":        int(input_bytes),
            "output_files":       int(output_files),
            "output_bytes":       int(output_bytes),
            "expansion_ratio":    (
                float(output_files) / float(input_files) if input_files > 0 else 0.0
            ),
            "was_split":          bool(was_split),
            "stubs_dropped":      int(stubs_dropped),
            "duplicates_dropped": int(duplicates_dropped),
            "notes":              (notes or "")[:200] if notes else None,
            "recorded_at":        time.time(),
        }
        try:
            await r.set(post_key(self.run_id), json.dumps(payload), ex=TTL_S)
        except Exception as e:
            logger.info(f"[progress] record_post skipped: {e}")

    async def finish(self, status: str = "done") -> None:
        """status ∈ {done, failed, aborted, downgrade, cancelled}."""
        self._state["status"] = status
        await self._flush(force = True)

    async def close(self) -> None:
        if self._r is not None:
            try:
                await self._r.aclose()
            except Exception:
                pass
            self._r = None


async def read_progress(r: redis_aio.Redis, run_id: str) -> Optional[dict]:
    try:
        raw = await r.get(progress_key(run_id))
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
        raw_list = await r.lrange(url_records_key(run_id), 0, -1)
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
        raw = await r.get(post_key(run_id))
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
