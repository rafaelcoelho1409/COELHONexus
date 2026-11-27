"""Read-only YCS Query backend and schema resources."""
from __future__ import annotations
from ...tools.query import schemas, service


class YCSQueryResources:
    def backends(self) -> schemas.QueryBackendCatalog:
        return service.backend_catalog()

    async def schema(self, backend: str) -> schemas.QuerySchemaSnapshot:
        return await service.read_schema(backend)
