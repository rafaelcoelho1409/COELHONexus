"""Pydantic schemas for the tool-credentials HTTP contract."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SetToolKeyBody(BaseModel):
    """Raw key travels browser→FastAPI ONCE on save; never returned in a
    response (status responses carry only `has_key` / `source` / `last4`)."""

    api_key: str = Field(min_length=1)
