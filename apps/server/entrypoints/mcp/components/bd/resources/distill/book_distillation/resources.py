"""Resource handlers for BD Distill progress, artifacts, and retrieval schemas."""
from __future__ import annotations
import domains
from .... import tools

from typing import Any


class BookDistillationResources:
    """Expose concise run progress and persisted Distill evidence."""

    async def current_run(self, book_id: str) -> dict:
        return await tools.distill.book_distillation.service.read_current_run(book_id)

    async def run(self, book_id: str, run_id: str) -> dict:
        return await tools.distill.book_distillation.service.read_run(book_id, run_id)

    async def report(self, book_id: str, run_id: str) -> dict:
        return await tools.distill.book_distillation.service.read_report(book_id, run_id)

    async def retrieval_context(self, book_id: str, run_id: str) -> dict:
        return await tools.distill.book_distillation.service.read_retrieval_context(book_id, run_id)

    def elasticsearch_schema(self) -> dict[str, Any]:
        return domains.bd.rag.source.retrieval.domain.elasticsearch_schema()

    def qdrant_schema(self) -> dict[str, Any]:
        return domains.bd.rag.source.retrieval.domain.qdrant_schema()

    def neo4j_schema(self) -> dict[str, Any]:
        return domains.bd.rag.source.retrieval.domain.neo4j_schema()

    def web_search_policy(self) -> dict[str, Any]:
        return domains.bd.rag.source.retrieval.domain.web_search_policy()
