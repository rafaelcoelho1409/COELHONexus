"""FastMCP handler methods for Agentic RAG index lifecycle operations."""
from __future__ import annotations
from . import schemas, service

from typing import Literal


IndexNode = Literal[
    "pin_sources",
    "elasticsearch",
    "qdrant",
    "neo4j",
    "publish_retrieval_index",
]


class BDRetrievalIndexTools:
    """Expose typed BD Agentic RAG indexing lifecycle operations."""

    async def get_run(self, book_id: str, agentic_run_id: str) -> schemas.RetrievalIndexSummary:
        """Read durable progress and store counts for one Agentic RAG indexing run."""
        return schemas.RetrievalIndexSummary.model_validate(
            await service.read_run(book_id, agentic_run_id)
        )

    async def get_current_index(self, book_id: str) -> schemas.CurrentIndexSummary:
        """Read the published index summary that BD retrieval currently serves."""
        return schemas.CurrentIndexSummary.model_validate(
            await service.read_current_index(book_id)
        )

    async def start_index(self, book_id: str, agentic_run_id: str) -> schemas.IndexCommandResult:
        """Queue indexing after the matching Agentic Ingestion run publishes its book."""
        return schemas.IndexCommandResult.model_validate(
            await service.start_index(book_id, agentic_run_id)
        )

    async def resume_index(self, book_id: str, agentic_run_id: str) -> schemas.IndexCommandResult:
        """Resume a failed, cancelled, or stale Agentic RAG index run."""
        return schemas.IndexCommandResult.model_validate(
            await service.resume_index(book_id, agentic_run_id)
        )

    async def cancel_index(self, book_id: str, agentic_run_id: str) -> schemas.IndexCommandResult:
        """Request cooperative cancellation of a queued or running index build."""
        return schemas.IndexCommandResult.model_validate(
            await service.cancel_index(book_id, agentic_run_id)
        )

    async def replay_from_node(
        self,
        book_id: str,
        agentic_run_id: str,
        node_id: IndexNode,
        confirm_replay: bool,
    ) -> schemas.IndexCommandResult:
        """Replay an index node and dependent outputs after explicit confirmation."""
        if not confirm_replay:
            raise ValueError("confirm_replay must be true to replay retrieval-index outputs")
        return schemas.IndexCommandResult.model_validate(
            await service.replay_from_node(
                book_id,
                agentic_run_id,
                node_id,
            )
        )
