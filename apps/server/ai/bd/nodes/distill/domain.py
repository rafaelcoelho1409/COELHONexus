"""Pure state projections shared by Distill graph routes."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ... import graphs


def graph_identity(state: graphs.distill.states.DistillRunState) -> dict[str, Any]:
    """Keep run identity and source metadata when sending work to a graph branch."""
    return {
        key: state[key]
        for key in (
            "book_id",
            "run_id",
            "extraction_run_id",
            "ingestion_ai_run_id",
            "source_manifest_key",
            "source_manifest_sha256",
            "ingestion_ai_manifest_key",
            "ingestion_ai_manifest_sha256",
            "content_selection_key",
            "content_selection_sha256",
            "content_selection_revision",
            "expected_page_count",
            "selected_page_count",
            "run_created_at",
        )
        if key in state
    }
