"""settings router — Pydantic request/response models (HTTP contract)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class EndpointBody(BaseModel):
    """LLM endpoint config. `api_key=None` leaves the stored key unchanged;
    `api_key=""` clears it."""
    url: str = Field(min_length=1)
    model: str = "auto"
    api_key: str | None = None


class EmbeddingBody(BaseModel):
    """Embedding endpoint config — independent connection from chat's
    EndpointBody above, same shape. Can point at any OpenAI-compatible
    embedding service. `api_key=None` leaves the stored key unchanged;
    `api_key=""` clears it."""
    url: str = Field(min_length=1)
    model: str = "auto"
    api_key: str | None = None
