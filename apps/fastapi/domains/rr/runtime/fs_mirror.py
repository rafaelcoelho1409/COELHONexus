"""Redis mirror for the RR agent's per-scan virtual filesystem.

Why a mirror:

  The agent's fs lives in `tools/state.py` as a module-level dict —
  process-local to the Celery worker. FastAPI can't see it. After the
  Celery task finishes, the dict gets cleared in the task's finally
  block, so even within the same process the state is gone.

  Mirroring every fs_write to Redis (`rr:{scan_id}:fs:{path}`) gives:
    * FastAPI a live read path during in-flight scans — the per-node
      drawer's "Last scan output" sections can show real data.
    * A 6h replay window for terminal scans (matches the events snapshot
      TTL) so the drawer also works on past scans.

Best-effort: a Redis hiccup MUST NOT sink the underlying fs write.
Errors are warning-logged and swallowed; the local dict is the source of
truth at runtime, the mirror is purely a read-side affordance."""
from __future__ import annotations

import json
import logging
from typing import Any

from .keys import redis_url
from .params import (
    REDIS_CONNECT_TIMEOUT_S,
    REDIS_OP_TIMEOUT_S,
    SNAPSHOT_TTL_S,
)


logger = logging.getLogger(__name__)


# Reuse the snapshot TTL — same scope as the SSE replay window. Long
# enough that the drawer can introspect any scan reachable via its URL.
_FS_MIRROR_TTL_S: int = SNAPSHOT_TTL_S


def _key(scan_id: str, path: str) -> str:
    """Redis key for one fs entry. Path collisions are impossible since
    the agent's fs paths are namespaced (discovery/, extractions/, …)."""
    return f"rr:{scan_id}:fs:{path}"


def _index_key(scan_id: str) -> str:
    """Redis SET key tracking every path written for this scan, so the
    drawer can ask "what's in fs?" without scanning Redis."""
    return f"rr:{scan_id}:fs:index"


def mirror_write_sync(scan_id: str, path: str, value: Any) -> None:
    """Best-effort sync mirror — called from inside the @tool body which
    runs in the Celery worker's asyncio loop. Sync Redis client because
    the tool may not be awaitable in all subagent contexts."""
    if not scan_id or not path:
        return
    import redis as redis_sync
    try:
        r = redis_sync.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = REDIS_OP_TIMEOUT_S,
        )
        try:
            payload = json.dumps(value, default=str)
            pipe = r.pipeline(transaction=False)
            pipe.set(_key(scan_id, path), payload, ex=_FS_MIRROR_TTL_S)
            pipe.sadd(_index_key(scan_id), path)
            pipe.expire(_index_key(scan_id), _FS_MIRROR_TTL_S)
            pipe.execute()
        finally:
            r.close()
    except Exception as e:
        logger.warning(
            f"[rr-fs-mirror] sync write failed for {scan_id!r}/{path!r}: "
            f"{type(e).__name__}: {e}"
        )


async def mirror_read(scan_id: str, path: str) -> Any | None:
    """Read one fs entry. Returns the parsed JSON value or None on miss /
    Redis error. Used by `GET /scan/{id}/fs/{path}` to power the
    Pipeline-page drawer."""
    if not scan_id or not path:
        return None
    import redis.asyncio as redis_aio
    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = REDIS_OP_TIMEOUT_S,
    )
    try:
        raw = await r.get(_key(scan_id, path))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode() if isinstance(raw, bytes) else raw
    except Exception as e:
        logger.warning(
            f"[rr-fs-mirror] read failed for {scan_id!r}/{path!r}: "
            f"{type(e).__name__}: {e}"
        )
        return None
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def mirror_index(scan_id: str) -> list[str]:
    """List every fs path mirrored for this scan. Returns [] on miss /
    error. Used by the drawer to discover what's available before
    fetching individual entries."""
    if not scan_id:
        return []
    import redis.asyncio as redis_aio
    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout         = REDIS_OP_TIMEOUT_S,
    )
    try:
        raw = await r.smembers(_index_key(scan_id))
        return sorted(p.decode() if isinstance(p, bytes) else str(p) for p in raw)
    except Exception as e:
        logger.warning(
            f"[rr-fs-mirror] index read failed for {scan_id!r}: "
            f"{type(e).__name__}: {e}"
        )
        return []
    finally:
        try:
            await r.aclose()
        except Exception:
            pass
