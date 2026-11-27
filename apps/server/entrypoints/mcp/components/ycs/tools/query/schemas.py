"""Stable structured outputs for YCS Query MCP operations."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import params


QueryBackend = Literal["elasticsearch", "qdrant", "neo4j"]


class QueryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: QueryBackend
    query: str = Field(max_length=params.MAX_QUERY_CHARS)
    valid: bool
    validation_error: str | None = Field(default=None, max_length=params.MAX_VALIDATION_ERROR_CHARS)
    model: str | None = None
    attempts: int = Field(ge=1, le=2)
    schema_fingerprint: str = Field(min_length=64, max_length=64)


class QueryValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: QueryBackend
    valid: bool
    validation_error: str | None = Field(default=None, max_length=params.MAX_VALIDATION_ERROR_CHARS)
    schema_fingerprint: str = Field(min_length=64, max_length=64)


class QueryHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class QueryRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: QueryBackend
    app: Literal["ycs"] = "ycs"
    ok: bool
    error: str | None = None
    notes: list[str] = Field(default_factory=list, max_length=20)
    took_ms: int = Field(default=0, ge=0)
    total: int | None = Field(default=None, ge=0)
    hits: list[QueryHit] = Field(default_factory=list, max_length=params.MAX_QUERY_RESULT_HITS)
    truncated: bool = False


class QuerySchemaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: QueryBackend
    app: Literal["ycs"] = "ycs"
    label: str
    target: str
    live_schema_available: bool
    live_data_available: bool
    generated_at: str
    schema_fingerprint: str = Field(min_length=64, max_length=64)
    schema_data: dict[str, Any] = Field(default_factory=dict)


class QueryBackendDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: QueryBackend
    label: str
    query_format: str
    scope: str
    default_limit: int
    maximum_limit: int


class QueryBackendCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["ycs"] = "ycs"
    backends: list[QueryBackendDescription] = Field(default_factory=list)
