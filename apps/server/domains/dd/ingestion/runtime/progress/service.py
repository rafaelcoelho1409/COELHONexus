"""Per-run progress and per-framework single-flight lock (the cancel flag lives in `../cancel/`)."""

from __future__ import annotations
import domains, infra
from . import domain, errors, keys, params

import json
import logging
import time


logger = logging.getLogger(__name__)


async def acquire_lock(
    r: infra.redis.service.Redis,
    framework_slug: str,
    run_id: str,
    ttl_s: int = params.LOCK_QUEUED_S,
) -> bool:
    """SETNX with TTL. True if this run_id now holds the lock."""
    try:
        ok = await r.set(keys.lock_key(framework_slug), run_id, nx=True, ex=ttl_s)
    except Exception as e:
        logger.warning(f"[lock] acquire failed: {e}")
        return False
    return bool(ok)


async def renew_lock(
    r: infra.redis.service.Redis, framework_slug: str, run_id: str
) -> bool:
    """Keep this run's lock alive for another LOCK_LEASE_S: extend it while it is still ours, or take it again when it lapsed
    (a stall longer than the lease) and nobody else has it. False when another run holds it now — this run no longer owns the
    framework."""
    try:
        if await infra.redis.service.extend_if_owner(
            r, keys.lock_key(framework_slug), run_id, params.LOCK_LEASE_S
        ):
            return True
    except Exception as e:
        logger.warning(f"[lock] renew failed: {e}")
        return False
    return await acquire_lock(r, framework_slug, run_id, ttl_s=params.LOCK_LEASE_S)


async def read_lock(r: infra.redis.service.Redis, framework_slug: str) -> str | None:
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
        return await infra.redis.service.release_if_owner(
            r, keys.lock_key(framework_slug), run_id
        )
    except Exception as e:
        logger.warning(f"[lock] release failed: {e}")
        return False


async def find_other_running(
    r: infra.redis.service.Redis, framework_slug: str
) -> tuple[str, str] | None:
    """`(slug, run_id)` of an ingestion running for ANOTHER framework (one ingestion at a time), else None."""
    return await infra.redis.service.first_held(
        r, keys.lock_prefix(), skip=framework_slug
    )


async def list_active(r: infra.redis.service.Redis) -> list[dict]:
    """In-flight ingestions: the lock is held AND progress says queued, running, or idle. A lock with no
    progress record (a rare race) is not surfaced."""
    active: list[dict] = []
    async for slug, run_id in infra.redis.service.iter_held(r, keys.lock_prefix()):
        if not run_id:
            continue
        progress = await read_progress(r, run_id)
        if domain.is_active(progress):
            active.append({"slug": slug, "run_id": run_id, "progress": progress})
    return active


class Progress:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._r: infra.redis.service.Redis | None = None
        self._last_flush = 0.0
        self._last_cancel_check = 0.0
        self._state: dict = domain.initial_state(time.time())
        self._restored = False
        self._active_stage: str | None = None

    async def _client(self) -> infra.redis.service.Redis | None:
        if self._r is None:
            try:
                self._r = infra.redis.service.new_redis()
            except Exception as e:
                logger.warning(f"[progress] Redis init failed: {e}")
                return None
        return self._r

    async def _restore_state(self) -> None:
        """Keep phase snapshots when source fallback creates a fresh Progress for the same run."""
        if self._restored:
            return
        self._restored = True
        r = await self._client()
        if r is None:
            return
        saved = await _read_json(r, keys.progress_key(self.run_id))
        if not isinstance(saved, dict):
            return
        restored = domain.initial_state(time.time())
        restored.update(saved)
        saved_stages = saved.get("stages")
        if isinstance(saved_stages, dict):
            for name, phase in saved_stages.items():
                if name in restored["stages"] and isinstance(phase, dict):
                    restored["stages"][name].update(phase)
        self._state = restored

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
                keys.progress_key(self.run_id), json.dumps(self._state), ex=params.TTL_S
            )
        except Exception as e:
            logger.info(f"[progress] write skipped: {e}")

    async def start(self, tier: str, total: int) -> None:
        await self._restore_state()
        stage = (
            "agentic"
            if tier == "agentic_normalization"
            else "extraction"
            if tier
            in {
                "llms_full",
                "llms_txt",
                "sitemap",
                "docs",
                "playwright",
                "github",
            }
            else None
        )
        if stage is not None:
            self._active_stage = stage
        self._state.update(
            tier=tier,
            total=max(0, int(total)),
            current=0,
            last_url="",
            status=params.STATUS_RUNNING,
        )
        if stage is not None:
            phases = self._state.setdefault("stages", {})
            phase = phases.setdefault(stage, {})
            phase.update(
                status="running",
                current=0,
                total=max(0, int(total)),
                error=None,
            )
            if stage == "extraction":
                phase.update(tier=tier, last_url="")
            else:
                phase.update(current_node=None, node_status={}, node_progress={})
        await self._flush(force=True)

    async def queue_agentic(self, total: int) -> None:
        """Publish a visible queued state before a standalone Agentic AI Celery task starts."""
        await self._restore_state()
        self._active_stage = "agentic"
        page_count = max(0, int(total))
        self._state.update(
            tier="agentic_normalization",
            current=0,
            total=page_count,
            last_url="",
            status=params.STATUS_QUEUED,
        )
        stages = self._state.setdefault("stages", {})
        extraction = stages.setdefault("extraction", {})
        extraction.update(
            status="done", current=page_count, total=page_count, error=None
        )
        agentic = stages.setdefault("agentic", {})
        agentic.update(
            status=params.STATUS_QUEUED,
            current=0,
            total=page_count,
            current_node=None,
            node_status={},
            node_progress={},
            error=None,
        )
        await self._flush(force=True)

    async def update_total(self, total: int) -> None:
        self._state["total"] = max(0, int(total))
        if self._active_stage is not None:
            phase = self._state.get("stages", {}).get(self._active_stage)
            if isinstance(phase, dict):
                phase["total"] = max(0, int(total))
        await self._flush(force=True)

    async def update(self, current: int, last_url: str = "") -> None:
        self._state.update(
            current=max(0, int(current)),
            last_url=(last_url or "")[: params.LAST_URL_MAX_CHARS],
        )
        if self._active_stage is not None:
            phase = self._state.get("stages", {}).get(self._active_stage)
            if isinstance(phase, dict):
                phase["current"] = max(0, int(current))
                if self._active_stage == "extraction":
                    phase["last_url"] = (last_url or "")[: params.LAST_URL_MAX_CHARS]
        await self._flush(force=False)

    async def complete_stage(
        self,
        stage: str,
        *,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """Mark one visible pipeline phase complete without changing the overall run status."""
        await self._restore_state()
        phase = self._state.setdefault("stages", {}).setdefault(stage, {})
        phase["status"] = "done"
        if total is not None:
            phase["total"] = max(0, int(total))
        if current is not None:
            phase["current"] = max(0, int(current))
        elif total is not None:
            phase["current"] = max(0, int(total))
        phase["error"] = None
        await self._flush(force=True)

    async def update_agentic_node(
        self,
        node: str,
        *,
        status: str | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """Persist graph-node status and per-node page counts for the ingestion Pipeline tab."""
        await self._restore_state()
        phases = self._state.setdefault("stages", {})
        phase = phases.setdefault("agentic", {})
        phase.setdefault("node_status", {})
        phase.setdefault("node_progress", {})
        if status is not None:
            phase["current_node"] = (
                node if status == "running" else phase.get("current_node")
            )
            phase["node_status"][node] = status
            if status in {"done", "failed"} and phase.get("current_node") == node:
                phase["current_node"] = None
        if current is not None or total is not None:
            node_progress = phase["node_progress"].setdefault(node, {})
            if current is not None:
                node_progress["current"] = max(0, int(current))
            if total is not None:
                node_progress["total"] = max(0, int(total))
        await self._flush(force=status is not None)

    async def check_cancelled(self) -> bool:
        """≤1/s polling; tiers call between fetches → raise IngestCancelled."""
        now = time.time()
        if (now - self._last_cancel_check) < params.CANCEL_POLL_THROTTLE_S:
            return False
        self._last_cancel_check = now
        r = await self._client()
        if r is None:
            return False
        return await domains.dd.ingestion.runtime.cancel.service.is_cancelled(
            r, self.run_id
        )

    async def raise_if_cancelled(self) -> None:
        if await self.check_cancelled():
            raise errors.IngestCancelled(self.run_id)

    async def record_url(
        self,
        url: str,
        *,
        status: str,
        tier: str | None = None,
        http_code: int | None = None,
        fetch_ms: int | None = None,
        bytes_fetched: int | None = None,
        extracted_chars: int | None = None,
        error_msg: str | None = None,
    ) -> None:
        r = await self._client()
        if r is None:
            return
        rec = domain.url_record(
            url,
            status=status,
            tier=tier or self._state.get("tier"),
            http_code=http_code,
            fetch_ms=fetch_ms,
            bytes_fetched=bytes_fetched,
            extracted_chars=extracted_chars,
            error_msg=error_msg,
            now=time.time(),
        )
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
        tier: str | None = None,
        input_files: int,
        input_bytes: int,
        output_files: int,
        output_bytes: int,
        was_split: bool,
        stubs_dropped: int = 0,
        duplicates_dropped: int = 0,
        notes: str | None = None,
    ) -> None:
        r = await self._client()
        if r is None:
            return
        payload = domain.post_summary(
            tier=tier or self._state.get("tier"),
            input_files=input_files,
            input_bytes=input_bytes,
            output_files=output_files,
            output_bytes=output_bytes,
            was_split=was_split,
            stubs_dropped=stubs_dropped,
            duplicates_dropped=duplicates_dropped,
            notes=notes,
            now=time.time(),
        )
        try:
            await r.set(
                keys.post_key(self.run_id), json.dumps(payload), ex=params.TTL_S
            )
        except Exception as e:
            logger.info(f"[progress] record_post skipped: {e}")

    async def finish(self, status: str = "done", error: str | None = None) -> None:
        """status ∈ {done, failed, aborted, downgrade, cancelled}.

        `error` surfaces the terminal reason to `GET /runs/{id}` pollers
        (2026-09-26: Qdrant Tier-1 404 failed with the reason only in the
        Celery result — the page sat on Loading… forever). Truncated to
        500 chars like url-record `error_msg`."""
        await self._restore_state()
        self._state["status"] = status
        if error:
            self._state["error"] = error[: params.ERROR_MAX_CHARS]
        if self._active_stage is not None:
            phase = self._state.get("stages", {}).get(self._active_stage)
            if isinstance(phase, dict) and phase.get("status") in {"running", "queued"}:
                phase["status"] = status
                if error:
                    phase["error"] = error[: params.ERROR_MAX_CHARS]
        elif self._state.get("stages", {}).get("agentic", {}).get("status") == "queued":
            phase = self._state["stages"]["agentic"]
            phase["status"] = status
            if error:
                phase["error"] = error[: params.ERROR_MAX_CHARS]
        await self._flush(force=True)

    async def close(self) -> None:
        if self._r is not None:
            try:
                await self._r.aclose()
            except Exception:
                pass
            self._r = None


async def _read_json(r: infra.redis.service.Redis, key: str) -> dict | None:
    """The JSON object stored under `key`, or None when there is none, Redis fails or it cannot be read."""
    try:
        raw = await r.get(key)
    except Exception:  # noqa: BLE001 — a progress read never fails the caller; "no progress" is its answer
        return None
    return domains.dd.ingestion.storage.domain.parse_json(raw, None)


async def read_progress(r: infra.redis.service.Redis, run_id: str) -> dict | None:
    return await _read_json(r, keys.progress_key(run_id))


async def read_url_records(r: infra.redis.service.Redis, run_id: str) -> list[dict]:
    try:
        raw_list = await r.lrange(keys.url_records_key(run_id), 0, -1)
    except Exception:  # noqa: BLE001 — a progress read never fails the caller
        return []
    records = (
        domains.dd.ingestion.storage.domain.parse_json(raw, None)
        for raw in raw_list or []
    )
    return [record for record in records if record is not None]


async def read_post(r: infra.redis.service.Redis, run_id: str) -> dict | None:
    return await _read_json(r, keys.post_key(run_id))
