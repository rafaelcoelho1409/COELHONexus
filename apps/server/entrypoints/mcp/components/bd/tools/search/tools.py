"""FastMCP handler methods for read-only BD retrieval capabilities."""
from __future__ import annotations
from . import params, schemas, service


class BDSearchTools:
    """Bind the same handler contract to external and trusted internal servers."""

    def __init__(self, *, trusted_in_process: bool = False):
        self.trusted_in_process = trusted_in_process

    async def search_elasticsearch(
        self,
        book_id: str,
        run_id: str,
        query: str,
    ) -> schemas.RetrievalResult:
        await service.authorize_book(book_id, self.trusted_in_process)
        return await service.search_store("elasticsearch", book_id, run_id, query)

    async def search_qdrant(
        self,
        book_id: str,
        run_id: str,
        query: str,
    ) -> schemas.RetrievalResult:
        await service.authorize_book(book_id, self.trusted_in_process)
        return await service.search_store("qdrant", book_id, run_id, query)

    async def search_neo4j(
        self,
        book_id: str,
        run_id: str,
        query: str,
    ) -> schemas.RetrievalResult:
        await service.authorize_book(book_id, self.trusted_in_process)
        return await service.search_store("neo4j", book_id, run_id, query)

    async def search_web(self, query: str) -> schemas.RetrievalResult:
        if not params.WEB_SEARCH_ENABLED:
            return schemas.RetrievalResult(
                status = "disabled", source = "external_search", query = "", evidence = [],
            )
        await service.authorize_web(self.trusted_in_process)
        return await service.search_web(query, self.trusted_in_process)
