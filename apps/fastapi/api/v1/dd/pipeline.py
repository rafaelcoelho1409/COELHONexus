"""Cross-stage pipeline-state introspection.

The Docs Distiller is a 4-stage pipeline:

  Catalog → Ingestion → Planner → Synth (chapter render = "Study")

Two read concerns served from this module:

  1. PER-SLUG CACHE STATE — `GET /pipeline/{slug}/state`
     For a given framework, which downstream stages have cached
     artifacts? Drives cascade-delete confirm dialogs ("Wipe Planner
     — will ALSO erase the cached Synth+Study") and skip-cascade
     short-circuits. Probes MinIO HEAD on:
       ingestion: ingestion/{slug}/manifest.json
       planner:   planner/{slug}/plan-latest.json
       synth:     anything under synth/{slug}/
       study:     at least one synth/{slug}/{cid}/render-latest.json

  2. GLOBAL ACTIVITY — `GET /pipeline/active`
     What's running RIGHT NOW across the whole deployment? Drives the
     cross-stage proactive gate (Planner and Synth must NOT run
     simultaneously — they fight for the same free-tier LLM
     resources). Reads the `dd:planner:lock:*` and `dd:synth:lock:*`
     namespaces set by their respective POST endpoints.

Both endpoints return JSON dicts; the caller decides what to do.
"""
import logging
import os

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException

from domains.dd.ingestion.storage import get_storage


logger = logging.getLogger(__name__)
router = APIRouter()


def _redis_url() -> str:
    """Same helper shape as runs.py / planner.cancel / synth.cancel —
    duplicated here to keep pipeline.py free of cross-module imports
    that could create circular dependencies at startup."""
    host = os.environ.get(
        "REDIS_HOST", "redis-master.redis.svc.cluster.local",
    )
    port = os.environ.get("REDIS_PORT", "6379")
    pwd = os.environ.get("REDIS_PASSWORD", "")
    return (
        f"redis://:{pwd}@{host}:{port}" if pwd
        else f"redis://{host}:{port}"
    )


@router.get("/active")
async def pipeline_active() -> dict:
    """Return what's currently running globally — drives the FastHTML
    cross-stage proactive gate (Planner and Synth must not run
    simultaneously because they compete for the same LLM rotator pool
    and degrade each other's output quality).

    Returns:
        ``{"planner": {"slug": str, "thread_id": str} | None,
           "synth":   {"slug": str, "thread_id": str} | None}``

    `None` for a stage means nothing is running for that stage. There's
    at most ONE running planner and ONE running synth across the whole
    deployment (enforced by the single-flight locks at POST /planner
    and POST /synth), so we return the first lock found and stop.
    Ingestion is deliberately omitted — it's orthogonal to the
    Planner/Synth cross-stage block.
    """
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
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
    """Return a flat dict of `{ingestion, planner, synth, study}` booleans
    indicating which pipeline stages have cached artifacts for ``slug``.

    Used by the frontend wipe / delete dialogs to surface accurate
    cascade-impact messaging ("Wipe Planner will also delete the cached
    Synth + Study for this framework").
    """
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
        """True iff at least one object exists under ``prefix``. Uses the
        existing list helper with a fast bail (max 1 key)."""
        try:
            keys = await minio.list(prefix)
        except Exception as e:
            logger.info(f"[pipeline-state] list({prefix!r}) failed: {e}")
            return False
        return bool(keys)

    ingestion = await _exists(f"ingestion/{slug}/manifest.json")
    planner   = await _exists(f"planner/{slug}/plan-latest.json")
    synth     = await _has_any(f"synth/{slug}/")
    # Study = at least one chapter actually rendered (render-latest.json
    # exists under a chapter folder). We do a single list pass and scan
    # the keys — cheaper than per-chapter HEAD probes when the user
    # has many chapters but none rendered yet.
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
