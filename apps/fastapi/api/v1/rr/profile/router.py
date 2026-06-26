"""Profile endpoints — per-profile Postgres state."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from domains.rr.stores.postgres import reset_seen


logger = logging.getLogger(__name__)


router = APIRouter()


class ResetSeenResponse(BaseModel):
    """Body of `POST /profile/{profile_id}/reset-seen`. `deleted` = rows dropped from `radar_seen`."""
    profile_id: str = Field(..., description="The profile that was reset.")
    deleted:    int = Field(..., ge=0,
                            description="Rows removed from radar_seen.")


@router.post(
    "/profile/{profile_id}/reset-seen",
    response_model = ResetSeenResponse,
    status_code    = 200,
)
async def reset_seen_endpoint(profile_id: str) -> ResetSeenResponse:
    """Truncate radar_seen for one profile so the next scan treats every paper as new. Only touches radar_seen."""
    profile_id = (profile_id or "").strip()
    if not profile_id:
        raise HTTPException(status_code=400, detail="profile_id is required")
    if len(profile_id) > 64:
        raise HTTPException(status_code=400, detail="profile_id too long")
    try:
        n = await reset_seen(profile_id)
    except Exception as e:
        logger.exception(f"[rr-api] reset-seen failed for {profile_id!r}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    logger.info(f"[rr-api] POST /profile/{profile_id}/reset-seen deleted={n}")
    return ResetSeenResponse(profile_id=profile_id, deleted=n)
