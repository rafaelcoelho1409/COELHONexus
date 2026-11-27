"""Typed FastMCP handler methods for BD Query."""
from __future__ import annotations
from . import params, schemas, service

from typing import Annotated

from pydantic import Field


class BDQueryTools:
    """Expose the same schema-aware Query workflow used by the BD workbench."""

    def get_catalog(self) -> schemas.QueryBackendCatalog:
        return service.backend_catalog()

    async def get_schema(self, backend: schemas.QueryBackend) -> schemas.QuerySchemaSnapshot:
        return await service.read_schema(backend)

    async def draft(
        self,
        backend: schemas.QueryBackend,
        user_request: Annotated[
            str, Field(min_length=1, max_length=params.MAX_PROMPT_CHARS)
        ],
        previous_query: Annotated[str, Field(max_length=params.MAX_QUERY_CHARS)] = "",
    ) -> schemas.QueryDraft:
        return await service.draft_query(backend, user_request, previous_query)

    async def validate(
        self,
        backend: schemas.QueryBackend,
        query: Annotated[str, Field(min_length=1, max_length=params.MAX_QUERY_CHARS)],
    ) -> schemas.QueryValidation:
        return await service.validate_query(backend, query)

    async def run(
        self,
        backend: schemas.QueryBackend,
        query: Annotated[str, Field(min_length=1, max_length=params.MAX_QUERY_CHARS)],
    ) -> schemas.QueryRunResult:
        return await service.run_query(backend, query)
