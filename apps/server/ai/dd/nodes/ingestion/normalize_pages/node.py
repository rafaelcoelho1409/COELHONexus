"""LangGraph node adapter for concurrent ingestion page normalization."""
from __future__ import annotations

from . import service
from .... import graphs

from typing import Any


async def normalize_pages(
    state: graphs.ingestion.state.IngestionState,
    read_page: service.PageReader,
    artifact_exists: service.ArtifactExists,
    read_artifact: service.ArtifactReader,
    write_artifact: service.ArtifactWriter,
    repairer: service.Repairer,
    progress: service.CancellationCheck | None,
    page_done: service.PageProgress,
) -> dict[str, Any]:
    """Run the normalization service as the ingestion graph's repair node."""
    return await service.normalize_pages(
        state,
        read_page,
        artifact_exists,
        read_artifact,
        write_artifact,
        repairer,
        progress,
        page_done,
    )
