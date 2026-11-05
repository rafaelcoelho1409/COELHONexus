"""LangGraph node adapter for concurrent ingestion page audits."""
from __future__ import annotations

from . import service
from .... import graphs

from typing import Any


async def audit_pages(
    state: graphs.ingestion.state.IngestionState,
    read_page: service.PageReader,
    artifact_exists: service.ArtifactExists,
    read_artifact: service.ArtifactReader,
    write_artifact: service.ArtifactWriter,
    auditor: service.Auditor,
    auditor_files: dict[str, Any],
    progress: service.CancellationCheck | None,
    page_done: service.PageProgress,
) -> dict[str, Any]:
    """Run the batch audit service as the ingestion graph's audit node."""
    return await service.audit_pages(
        state,
        read_page,
        artifact_exists,
        read_artifact,
        write_artifact,
        auditor,
        auditor_files,
        progress,
        page_done,
    )
