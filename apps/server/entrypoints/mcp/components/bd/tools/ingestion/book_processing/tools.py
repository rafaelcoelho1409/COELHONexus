"""FastMCP handler methods for Agentic Ingestion lifecycle operations."""
from __future__ import annotations
from . import schemas, service

from typing import Annotated, Literal

from pydantic import Field


IngestionNode = Literal[
    "audit_batch",
    "classify_content",
    "normalize_metadata",
    "plan_repairs",
    "repair_pages",
    "enrich_visuals",
    "materialize_processed_book",
    "reconcile_contents",
    "publish",
]


class BDIngestionTools:
    """Expose typed BD Agentic Ingestion lifecycle operations."""

    async def get_run(self, book_id: str, agentic_run_id: str) -> schemas.IngestionRunSummary:
        """Read a compact Agentic Ingestion run status and graph progress snapshot."""
        return schemas.IngestionRunSummary.model_validate(
            await service.read_run(book_id, agentic_run_id)
        )

    async def get_quality_summary(
        self,
        book_id: str,
        agentic_run_id: str,
    ) -> schemas.IngestionQualitySummary:
        """Read aggregate quality counts without returning page text or storage keys."""
        return schemas.IngestionQualitySummary.model_validate(
            await service.read_quality(book_id, agentic_run_id)
        )

    async def start_run(
        self,
        book_id: str,
        idempotency_key: Annotated[str, Field(min_length = 1, max_length = 160)],
        extraction_run_id: str | None = None,
    ) -> schemas.IngestionCommandResult:
        """Queue Agentic Ingestion against the completed extraction; reuse the key on retries."""
        return schemas.IngestionCommandResult.model_validate(
            await service.start_run(
                book_id,
                extraction_run_id,
                idempotency_key,
            )
        )

    async def resume_run(self, book_id: str, agentic_run_id: str) -> schemas.IngestionCommandResult:
        """Resume a failed or cancelled ingestion run from its saved checkpoint."""
        return schemas.IngestionCommandResult.model_validate(
            await service.resume_run(book_id, agentic_run_id)
        )

    async def cancel_run(self, book_id: str, agentic_run_id: str) -> schemas.IngestionCommandResult:
        """Request cooperative cancellation of a queued or running ingestion graph."""
        return schemas.IngestionCommandResult.model_validate(
            await service.cancel_run(book_id, agentic_run_id)
        )

    async def replay_from_node(
        self,
        book_id: str,
        agentic_run_id: str,
        node_id: IngestionNode,
        confirm_replay: bool,
    ) -> schemas.IngestionCommandResult:
        """Replay a node and descendants; this can clear outputs and incur new model cost."""
        if not confirm_replay:
            raise ValueError("confirm_replay must be true to replay ingestion outputs")
        return schemas.IngestionCommandResult.model_validate(
            await service.replay_from_node(
                book_id,
                agentic_run_id,
                node_id,
            )
        )
