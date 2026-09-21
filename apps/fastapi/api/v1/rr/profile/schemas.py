"""profile schemas — Pydantic request/response models (HTTP contract)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResetSeenResponse(BaseModel):
    """Body of `POST /profile/{profile_id}/reset-seen`. `deleted` = rows dropped from `radar_seen`."""
    profile_id: str = Field(..., description="The profile that was reset.")
    deleted:    int = Field(..., ge=0,
                            description="Rows removed from radar_seen.")
