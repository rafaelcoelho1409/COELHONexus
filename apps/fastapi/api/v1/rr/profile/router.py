"""Profile endpoints — actions over the per-profile state in Postgres.

Currently exposes one action:

  POST /profile/{profile_id}/reset-seen
      Truncates the `radar_seen` rows for that profile so the next scan's
      `diff_vs_seen` treats every paper as new. Operator-triggered via the
      Pipeline-page UI; never invoked by the scan pipeline itself.

Following docs/CODE-CONVENTIONS.md §service — routers stay THIN: validate
inputs, dispatch to the store, shape the response. No business logic here.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from domains.rr.stores.postgres import reset_seen


logger = logging.getLogger(__name__)


router = APIRouter()


class ResetSeenResponse(BaseModel):
    """Body of `POST /profile/{profile_id}/reset-seen`. `deleted` is the
    number of rows that were dropped from `radar_seen` — useful as a sanity
    check ("we cleared 47 prior arxiv_ids, scans should now show is_new=true
    again")."""
    profile_id: str = Field(..., description="The profile that was reset.")
    deleted:    int = Field(..., ge=0,
                            description="Rows removed from radar_seen.")


@router.post(
    "/profile/{profile_id}/reset-seen",
    response_model = ResetSeenResponse,
    status_code    = 200,
)
async def reset_seen_endpoint(profile_id: str) -> ResetSeenResponse:
    """Wipe the seen-set for one profile. Idempotent (a second call after
    truncation returns deleted=0). Does NOT touch radar_scans / radar_findings
    / MinIO digests / Neo4j — only the radar_seen membership table."""
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
