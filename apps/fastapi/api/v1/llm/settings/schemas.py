"""settings router — Pydantic request/response models (HTTP contract)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KeyBody(BaseModel):
    api_key: str = Field(min_length=1)


class EnableBody(BaseModel):
    enabled: bool


class ModelsBody(BaseModel):
    mode: Literal["all", "custom"]
    selected: list[str] = Field(default_factory=list)
