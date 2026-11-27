"""Register BD Distill run, artifact, and retrieval resources."""
from __future__ import annotations
from . import resources

from typing import Any

from fastmcp import FastMCP


def register_resources(mcp: FastMCP) -> None:
    """Register read-only progress and output resources for Distill runs."""
    handlers = resources.BookDistillationResources()

    @mcp.resource(
        "bd://books/{book_id}/distill/current",
        name="bd_distill_current_run",
        title="Current BD Distill Run",
        description="Compact status and graph progress for the current Distill run of a book.",
        mime_type="application/json",
        tags={"bd", "distill", "run"},
    )
    async def current_run(book_id: str) -> dict[str, Any]:
        """Return current Distill progress without internal storage keys.

        Args:
            book_id: BD book identifier whose current Distill run should be read.
        """
        return await handlers.current_run(book_id)

    @mcp.resource(
        "bd://books/{book_id}/distill/runs/{run_id}",
        name="bd_distill_run",
        title="BD Distill Run",
        description="Compact run status and per-node progress for one Distill workflow.",
        mime_type="application/json",
        tags={"bd", "distill", "run"},
    )
    async def run(book_id: str, run_id: str) -> dict[str, Any]:
        """Return status for one Distill run without internal storage keys.

        Args:
            book_id: BD book identifier that owns the Distill run.
            run_id: BD Distill run identifier to read.
        """
        return await handlers.run(book_id, run_id)

    @mcp.resource(
        "bd://books/{book_id}/distill/runs/{run_id}/report",
        name="bd_distill_report",
        title="BD Distill Learning Guide",
        description="Validated, source-cited Learning Guide from a completed Distill run.",
        mime_type="application/json",
        tags={"bd", "distill", "report"},
    )
    async def report(book_id: str, run_id: str) -> dict[str, Any]:
        """Return the validated final report, including chapter guides and review metadata.

        Args:
            book_id: BD book identifier that owns the Distill run.
            run_id: Completed BD Distill run whose report should be read.
        """
        return await handlers.report(book_id, run_id)

    @mcp.resource(
        "bd://books/{book_id}/distill/runs/{run_id}/retrieval",
        name="bd_distill_retrieval_evidence",
        title="BD Distill Retrieval Evidence",
        description="Persisted source results and fused evidence from the four parallel retrieval stages.",
        mime_type="application/json",
        tags={"bd", "distill", "retrieval", "evidence"},
    )
    async def retrieval_context(book_id: str, run_id: str) -> dict[str, Any]:
        """Return per-source retrieval results and the fused evidence list.

        Args:
            book_id: BD book identifier that owns the Distill run.
            run_id: BD Distill run whose retrieval evidence should be read.
        """
        return await handlers.retrieval_context(book_id, run_id)

    @mcp.resource(
        "bd://distill/book-retrieval/elasticsearch/schema",
        name="bd_distill_elasticsearch_schema",
        title="BD Distill Elasticsearch Retrieval Schema",
        description="Passage fields and bounded BM25 retrieval contract for Distill.",
        mime_type="application/json",
        tags={"bd", "distill", "retrieval", "elasticsearch", "schema"},
    )
    def elasticsearch_schema() -> dict[str, Any]:
        """Return the current passage-index schema and query contract."""
        return handlers.elasticsearch_schema()

    @mcp.resource(
        "bd://distill/book-retrieval/qdrant/schema",
        name="bd_distill_qdrant_schema",
        title="BD Distill Qdrant Retrieval Schema",
        description="Vector and payload fields plus bounded hybrid retrieval contract.",
        mime_type="application/json",
        tags={"bd", "distill", "retrieval", "qdrant", "schema"},
    )
    def qdrant_schema() -> dict[str, Any]:
        """Return the dense/sparse passage schema and scope contract."""
        return handlers.qdrant_schema()

    @mcp.resource(
        "bd://distill/book-retrieval/neo4j/schema",
        name="bd_distill_neo4j_schema",
        title="BD Distill Neo4j Retrieval Schema",
        description="Curated graph labels, properties, relationships, and mandatory scope.",
        mime_type="application/json",
        tags={"bd", "distill", "retrieval", "neo4j", "schema"},
    )
    def neo4j_schema() -> dict[str, Any]:
        """Return the indexed BD graph schema without live graph samples."""
        return handlers.neo4j_schema()

    @mcp.resource(
        "bd://distill/book-retrieval/web-search/policy",
        name="bd_distill_web_search_policy",
        title="BD Distill Web Search Policy",
        description="Usage limits and provenance requirements for external evidence.",
        mime_type="application/json",
        tags={"bd", "distill", "retrieval", "web-search", "policy"},
    )
    def web_search_policy() -> dict[str, Any]:
        """Return policy and provenance fields for the optional web lane."""
        return handlers.web_search_policy()
