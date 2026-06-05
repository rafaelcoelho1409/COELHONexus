"""Cross-stage pipeline-state introspection. Per-slug cache map +
global Planner/Synth lock status."""
import logging

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException

from domains.dd.ingestion.storage import get_storage
from domains.dd.planner.keys import redis_url


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/active")
async def pipeline_active() -> dict:
    """Drives the cross-stage proactive gate. Ingestion omitted — it's
    orthogonal to the Planner/Synth lock block."""
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )

    async def _first_lock(prefix: str) -> dict | None:
        cursor = 0
        while True:
            cursor, keys = await r.scan(
                cursor=cursor, match=f"{prefix}*", count=100,
            )
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                slug = ks.split(prefix, 1)[-1]
                val = await r.get(ks)
                if val is None:
                    continue
                return {
                    "slug": slug,
                    "thread_id": (
                        val.decode() if isinstance(val, bytes) else val
                    ),
                }
            if cursor == 0:
                return None

    try:
        return {
            "planner": await _first_lock("dd:planner:lock:"),
            "synth":   await _first_lock("dd:synth:lock:"),
        }
    finally:
        await r.aclose()


@router.get("/{slug}/state")
async def pipeline_state(slug: str) -> dict:
    """Drives wipe-cascade dialogs ("Wipe Planner also deletes Synth")."""
    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )

    minio = get_storage()

    async def _exists(key: str) -> bool:
        try:
            return await minio.exists(key)
        except Exception as e:
            logger.info(f"[pipeline-state] exists({key!r}) failed: {e}")
            return False

    async def _has_any(prefix: str) -> bool:
        try:
            keys = await minio.list(prefix)
        except Exception as e:
            logger.info(f"[pipeline-state] list({prefix!r}) failed: {e}")
            return False
        return bool(keys)

    ingestion = await _exists(f"ingestion/{slug}/manifest.json")
    planner   = await _exists(f"planner/{slug}/plan-latest.json")
    synth     = await _has_any(f"synth/{slug}/")
    study = False
    if synth:
        try:
            keys = await minio.list(f"synth/{slug}/")
            study = any(k.endswith("/render-latest.json") for k in keys)
        except Exception as e:
            logger.info(f"[pipeline-state] study probe failed: {e}")
            study = False

    return {
        "slug": slug,
        "ingestion": ingestion,
        "planner":   planner,
        "synth":     synth,
        "study":     study,
    }
