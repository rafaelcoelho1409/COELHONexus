"""runs router — Pydantic request/response models (HTTP contract)."""
from __future__ import annotations

from pydantic import BaseModel


class StartRunBody(BaseModel):
    slug: str
    refresh: bool = False
