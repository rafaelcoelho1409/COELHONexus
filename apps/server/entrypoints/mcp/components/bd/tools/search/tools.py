"""FastMCP handler methods for read-only BD retrieval capabilities."""
from __future__ import annotations
from . import params, schemas, service


class BDSearchTools:
    """Expose BD retrieval and optional external search tools."""

    async def search_elasticsearch(
        self,
        book_id: str,
        run_id: str,
        query: str,
    ) -> schemas.RetrievalResult:
        return await service.search_store("elasticsearch", book_id, run_id, query)

    async def search_qdrant(
        self,
        book_id: str,
        run_id: str,
        query: str,
    ) -> schemas.RetrievalResult:
        return await service.search_store("qdrant", book_id, run_id, query)

    async def search_neo4j(
        self,
        book_id: str,
        run_id: str,
        query: str,
    ) -> schemas.RetrievalResult:
        return await service.search_store("neo4j", book_id, run_id, query)

    async def search_web(self, query: str) -> schemas.RetrievalResult:
        if not params.WEB_SEARCH_ENABLED:
            return schemas.RetrievalResult(
                status = "disabled", source = "external_search", query = "", evidence = [],
            )
        return await service.search_web(query)
