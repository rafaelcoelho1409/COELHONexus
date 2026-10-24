"""Per-run progress and per-framework single-flight lock (the cancel flag lives in `../cancel/`)."""
from __future__ import annotations
import domains, infra
from . import errors, keys, params

import json
import logging
import time
from typing import Optional


logger = logging.getLogger(__name__)


async def acquire_lock(
    r: infra.redis.service.Redis,
    framework_slug: str,
    run_id: str,
    ttl_s: int = params.LOCK_TTL_S,
) -> bool:
    """SETNX with TTL. True if this run_id now holds the lock."""
    try:
        ok = await r.set(keys.lock_key(framework_slug), run_id, nx=True, ex=ttl_s)
    except Exception as e:
        logger.warning(f"[lock] acquire failed: {e}")
        return False
    return bool(ok)


async def read_lock(r: infra.redis.service.Redis, framework_slug: str) -> Optional[str]:
    """run_id holding the lock, or None."""
    try:
        v = await r.get(keys.lock_key(framework_slug))
    except Exception:
        return None
    if not v:
        return None
    return v.decode() if isinstance(v, bytes) else v


async def release_lock(
    r: infra.redis.service.Redis,
    framework_slug: str,
    run_id: str,
) -> bool:
    """Release iff this run_id is the holder."""
    try:
        return await infra.redis.service.release_if_owner(r, keys.lock_key(framework_slug), run_id)
    except Exception as e:
        logger.warning(f"[lock] release failed: {e}")
        return False


class Progress:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._r: Optional[infra.redis.service.Redis] = None
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

    async def _client(self) -> Optional[infra.redis.service.Redis]:
        if self._r is None:
            try:
                self._r = infra.redis.service.new_redis()
            except Exception as e:
                logger.warning(f"[progress] Redis init failed: {e}")
                return None
        return self._r

    async def _flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_flush) < params.THROTTLE_S:
            return
        self._last_flush = now
        self._state["updated_at"] = now
        r = await self._client()
        if r is None:
            return
        try:
            await r.set(
                keys.progress_key(self.run_id), 
                json.dumps(self._state), 
                ex = params.TTL_S)
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
        if (now - self._last_cancel_check) < params.CANCEL_POLL_THROTTLE_S:
            return False
        self._last_cancel_check = now
        r = await self._client()
        if r is None:
            return False
        return await domains.dd.ingestion.runtime.cancel.service.is_cancelled(r, self.run_id)

    async def raise_if_cancelled(self) -> None:
        if await self.check_cancelled():
            raise errors.IngestCancelled(self.run_id)

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
            pipe.rpush(keys.url_records_key(self.run_id), json.dumps(rec))
            pipe.expire(keys.url_records_key(self.run_id), params.TTL_S)
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
            await r.set(keys.post_key(self.run_id), json.dumps(payload), ex=params.TTL_S)
        except Exception as e:
            logger.info(f"[progress] record_post skipped: {e}")

    async def finish(self, status: str = "done", error: str | None = None) -> None:
        """status ∈ {done, failed, aborted, downgrade, cancelled}.

        `error` surfaces the terminal reason to `GET /runs/{id}` pollers
        (2026-09-26: Qdrant Tier-1 404 failed with the reason only in the
        Celery result — the page sat on Loading… forever). Truncated to
        500 chars like url-record `error_msg`."""
        self._state["status"] = status
        if error:
            self._state["error"] = error[:500]
        await self._flush(force = True)

    async def close(self) -> None:
        if self._r is not None:
            try:
                await self._r.aclose()
            except Exception:
                pass
            self._r = None


async def read_progress(r: infra.redis.service.Redis, run_id: str) -> Optional[dict]:
    try:
        raw = await r.get(keys.progress_key(run_id))
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


async def read_url_records(r: infra.redis.service.Redis, run_id: str) -> list[dict]:
    try:
        raw_list = await r.lrange(keys.url_records_key(run_id), 0, -1)
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


async def read_post(r: infra.redis.service.Redis, run_id: str) -> Optional[dict]:
    try:
        raw = await r.get(keys.post_key(run_id))
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
