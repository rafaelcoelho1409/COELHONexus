"""Cross-stage pipeline-state introspection. Per-slug cache map +
global Planner/Synth lock status."""
import domains, infra

import logging

from fastapi import APIRouter, HTTPException


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/active")
async def pipeline_active() -> dict:
    """Drives the cross-stage proactive gate. Ingestion omitted — it's
    orthogonal to the Planner/Synth lock block."""
    return {
        "planner": await domains.dd.planner.runtime.dispatch.service.active_lock(),
        "synth":   await domains.dd.synth.runtime.dispatch.service.active_lock(),
    }


@router.get("/{slug}/state")
async def pipeline_state(slug: str) -> dict:
    """Drives wipe-cascade dialogs ("Wipe Planner also deletes Synth")."""
    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )
    minio = infra.minio.service.get_storage()
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

    ingestion = await _exists(domains.dd.ingestion.storage.keys.manifest_key(slug))
    planner   = await _exists(domains.dd.planner.keys.plan_latest_key(slug))
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
