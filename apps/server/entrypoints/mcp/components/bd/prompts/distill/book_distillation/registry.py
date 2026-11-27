"""Register BD Distill workflow and retrieval prompts."""
from __future__ import annotations
from . import domain

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Register full-run creation and completed-report review prompts."""

    @mcp.prompt(
        name = "bd_distill_create_learning_guide",
        title = "Create a BD Distill Learning Guide",
        description = "Queue the full Distill graph for a book and follow its report lifecycle.",
        tags = {"bd", "distill", "book-distillation", "workflow"},
    )
    def create_learning_guide(
        book_id: str,
        idempotency_key: str,
        request: str,
        ingestion_ai_run_id: str | None = None,
    ) -> str:
        """Create a complete source-grounded guide using the Distill workflow.

        Args:
            book_id: BD book identifier to distill.
            idempotency_key: Stable caller-generated key for safe retries.
            request: User's goal for the resulting guide.
            ingestion_ai_run_id: Optional completed Ingestion run to use as input.
        """
        return domain.build_create_prompt(
            book_id,
            idempotency_key,
            request,
            ingestion_ai_run_id,
        )

    @mcp.prompt(
        name = "bd_distill_review_learning_guide",
        title = "Review a BD Distill Learning Guide",
        description = "Inspect one Distill run and answer from its completed cited report.",
        tags = {"bd", "distill", "book-distillation", "report"},
    )
    def review_learning_guide(book_id: str, run_id: str, question: str) -> str:
        """Answer a question using the persisted report from one Distill run.

        Args:
            book_id: BD book identifier that owns the Distill run.
            run_id: BD Distill run to inspect.
            question: Question about the run or completed Learning Guide.
        """
        return domain.build_review_prompt(book_id, run_id, question)

    @mcp.prompt(
        name = "bd_distill_retrieve_elasticsearch",
        title = "Retrieve BD Book Evidence with Elasticsearch",
        description = "Read the BM25 schema, then retrieve lexical evidence from one BD book run.",
        tags = {"bd", "distill", "retrieval", "elasticsearch"},
    )
    def retrieve_elasticsearch(book_id: str, run_id: str, request: str) -> str:
        """Prepare bounded Elasticsearch retrieval from one published BD book run.

        Args:
            book_id: BD book identifier to search.
            run_id: Current Agentic Ingestion run identifier for the published index.
            request: Retrieval question or evidence need.
        """
        return domain.build_retrieval_prompt("elasticsearch", book_id, run_id, request)

    @mcp.prompt(
        name = "bd_distill_retrieve_qdrant",
        title = "Retrieve BD Book Evidence with Qdrant",
        description = "Read the vector schema, then retrieve hybrid semantic evidence from one BD book run.",
        tags = {"bd", "distill", "retrieval", "qdrant"},
    )
    def retrieve_qdrant(book_id: str, run_id: str, request: str) -> str:
        """Prepare bounded Qdrant retrieval from one published BD book run.

        Args:
            book_id: BD book identifier to search.
            run_id: Current Agentic Ingestion run identifier for the published index.
            request: Retrieval question or evidence need.
        """
        return domain.build_retrieval_prompt("qdrant", book_id, run_id, request)

    @mcp.prompt(
        name = "bd_distill_retrieve_neo4j",
        title = "Retrieve BD Book Evidence with Neo4j",
        description = "Read the graph schema, then retrieve scoped entity and relationship evidence.",
        tags = {"bd", "distill", "retrieval", "neo4j"},
    )
    def retrieve_neo4j(book_id: str, run_id: str, request: str) -> str:
        """Prepare schema-first, read-only graph retrieval from one BD book run.

        Args:
            book_id: BD book identifier to search.
            run_id: Current Agentic Ingestion run identifier for the published graph.
            request: Retrieval question or evidence need.
        """
        return domain.build_retrieval_prompt("neo4j", book_id, run_id, request)

    @mcp.prompt(
        name = "bd_distill_retrieve_web_search",
        title = "Retrieve External Context for BD Distill",
        description = "Read the web-search policy, then retrieve bounded external context when needed.",
        tags = {"bd", "distill", "retrieval", "web-search"},
    )
    def retrieve_web_search(book_id: str, run_id: str, request: str) -> str:
        """Prepare bounded external search while preserving source provenance.

        Args:
            book_id: BD book identifier associated with the Distill task.
            run_id: Agentic Distill run identifier associated with the task.
            request: Current external context or corroboration to find.
        """
        return domain.build_retrieval_prompt("web_search", book_id, run_id, request)
