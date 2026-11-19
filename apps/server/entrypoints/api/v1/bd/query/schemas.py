"""HTTP request and response contracts for the BD Query workbench."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

QueryBackend = Literal["elasticsearch", "qdrant", "neo4j"]


class NamespaceEntry(BaseModel):
    available: bool
    label: str = ""
    target: str = ""


class NamespaceResponse(BaseModel):
    apps: list[str] = Field(default_factory = list)
    backends: list[str] = Field(default_factory = list)
    matrix: dict[str, dict[str, NamespaceEntry]] = Field(default_factory = dict)


class RawQueryRequest(BaseModel):
    app: Literal["bd"] = "bd"
    body: str = Field(default = "", max_length = 100_000)


class RawQueryHit(BaseModel):
    summary: str = ""
    raw: dict[str, Any] = Field(default_factory = dict)


class RawQueryResponse(BaseModel):
    backend: QueryBackend
    app: Literal["bd"] = "bd"
    ok: bool
    error: str | None = None
    notes: list[str] = Field(default_factory = list)
    took_ms: int = 0
    total: int | None = None
    hits: list[RawQueryHit] = Field(default_factory = list)


class QuerySchemaResponse(BaseModel):
    backend: QueryBackend
    app: Literal["bd"] = "bd"
    cached_at: int = 0
    schema_payload: dict[str, Any] = Field(default_factory = dict, alias = "schema")


class QueryHistoryEntry(BaseModel):
    id: int
    backend: QueryBackend
    app: str
    body: str
    prompt: str = ""
    favorite: bool = False
    created_at: str = ""


class QueryHistoryResponse(BaseModel):
    items: list[QueryHistoryEntry] = Field(default_factory = list)
    total: int = 0


class SaveQueryHistoryRequest(BaseModel):
    backend: QueryBackend
    app: Literal["bd"] = "bd"
    body: str = Field(min_length = 1, max_length = 100_000)
    prompt: str = Field(default = "", max_length = 4_000)


class QueryAIRequest(BaseModel):
    app: Literal["bd"] = "bd"
    prompt: str = Field(min_length = 1, max_length = 4_000)
    previous: str = Field(default = "", max_length = 100_000)
