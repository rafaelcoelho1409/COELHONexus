"""FastMCP handlers for BD Distill workflow and retrieval operations."""
from __future__ import annotations
import domains
from . import params, schemas, service

from typing import Annotated, Literal

from pydantic import Field


DistillGraphNode = Literal[
    "prepare_units",
    "extract_unit",
    "verify_unit",
    "prepare_chapters",
    "synthesize_chapter",
    "review_chapter_coverage",
    "build_chapter_visuals",
    "prepare_retrieval",
    "elasticsearch",
    "qdrant",
    "neo4j",
    "web_search",
    "fuse_evidence",
    "synthesize_book",
    "research_context",
    "write_decision_brief",
    "publish_report",
]


class BDDistillTools:
    """Expose validated Distill run operations, not individual graph internals."""

    async def get_run(self, book_id: str, run_id: str) -> schemas.DistillRunSummary:
        return schemas.DistillRunSummary.model_validate(
            await service.read_run(book_id, run_id),
        )

    async def start_run(
        self,
        book_id: str,
        idempotency_key: Annotated[str, Field(min_length = 1, max_length = 160)],
        ingestion_ai_run_id: str | None = None,
    ) -> schemas.DistillCommandResult:
        return schemas.DistillCommandResult.model_validate(
            await service.start_run(book_id, idempotency_key, ingestion_ai_run_id),
        )

    async def resume_run(self, book_id: str, run_id: str) -> schemas.DistillCommandResult:
        return schemas.DistillCommandResult.model_validate(
            await service.resume_run(book_id, run_id),
        )

    async def cancel_run(self, book_id: str, run_id: str) -> schemas.DistillCommandResult:
        return schemas.DistillCommandResult.model_validate(
            await service.cancel_run(book_id, run_id),
        )

    async def replay_from_node(
        self,
        book_id: str,
        run_id: str,
        node_id: DistillGraphNode,
        confirm_replay: bool,
    ) -> schemas.DistillCommandResult:
        if not confirm_replay:
            raise ValueError("confirm_replay must be true to replay Distill outputs")
        return schemas.DistillCommandResult.model_validate(
            await service.replay_from_node(book_id, run_id, node_id),
        )


class DistillBookRetrievalTools:
    """Expose indexed-book retrieval and optional external search."""

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
        intent: domains.bd.rag.source.retrieval.schemas.Neo4jQueryIntent | None = None,
    ) -> schemas.RetrievalResult:
        return await service.search_store(
            "neo4j",
            book_id,
            run_id,
            query,
            neo4j_intent = intent,
        )

    async def search_web(self, query: str) -> schemas.RetrievalResult:
        if not params.WEB_SEARCH_ENABLED:
            return schemas.RetrievalResult(
                status = "disabled", source = "external_search", query = "", evidence = [],
            )
        return await service.search_web(query)
