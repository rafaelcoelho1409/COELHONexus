"""Resource handlers for BD Query backend metadata and live schemas."""
from __future__ import annotations
from ...tools.query import schemas, service


class BDQueryResources:
    """Expose bounded schema context for the three BD retrieval backends."""

    def backends(self) -> schemas.QueryBackendCatalog:
        return service.backend_catalog()

    async def schema(self, backend: str) -> schemas.QuerySchemaSnapshot:
        return await service.read_schema(backend)
