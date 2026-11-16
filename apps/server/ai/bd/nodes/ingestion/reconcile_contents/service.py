"""Build standardized contents from retained-page headings and publish the book manifest."""
from __future__ import annotations
import domains
from .... import graphs

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]
CancelCheck = Callable[[], Awaitable[bool]]


async def _read(reader: PageReader, key: str) -> str:
    value = reader(key)
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, str):
        raise ValueError(f"processed-book artifact {key} could not be read")
    return value


async def _candidate_headings(
    candidate: domains.bd.ingestion.ai.processed_book.schemas.ProcessedBookCandidate,
    read_page: PageReader,
) -> list[dict[str, Any]]:
    """Read selected-page headings, rebuilding legacy immutable candidates when needed."""
    if candidate.heading_candidates_built:
        return [item.model_dump(mode = "json") for item in candidate.heading_candidates]

    processed_book_domain = domains.bd.ingestion.ai.processed_book.domain
    semaphore = asyncio.Semaphore(domains.bd.ingestion.ai.params.PROCESSED_BOOK_READ_CONCURRENCY)

    async def page_evidence_for(page: Any) -> dict[str, Any]:
        async with semaphore:
            content = await _read(read_page, page.content_key)
        return {
            "source_page_number": page.source_page_number,
            "section_path": page.section_path,
            "role": page.role,
            "headings": processed_book_domain.markdown_headings(content),
            "evidence": processed_book_domain.heading_evidence(content, page.section_path),
        }

    page_evidence = await asyncio.gather(*(page_evidence_for(page) for page in candidate.pages))
    return processed_book_domain.canonical_heading_candidates(page_evidence)


async def reconcile_contents(
    state: graphs.ingestion.states.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    artifact_exists: Callable[[str], Awaitable[bool]],
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    """Generate the contents tree only from selected-page headings and publish its page map."""
    candidate_key = state.get("processed_book_candidate_key")
    if not isinstance(candidate_key, str):
        raise ValueError("contents generation requires a materialized processed-book candidate")
    candidate_content = await _read(read_page, candidate_key)
    candidate = domains.bd.ingestion.ai.processed_book.schemas.ProcessedBookCandidate.model_validate_json(
        candidate_content,
    )
    if (
        candidate.book_id != state.get("book_id")
        or candidate.extraction_run_id != state.get("extraction_run_id")
        or candidate.agentic_run_id != state.get("agentic_run_id")
        or candidate.selection_revision != state.get("content_selection_revision")
        or candidate.selection_sha256 != state.get("content_selection_sha256")
    ):
        raise ValueError("the processed-book candidate is not pinned to this ingestion run")

    if cancel_check is not None and await cancel_check():
        raise asyncio.CancelledError
    heading_candidates = await _candidate_headings(candidate, read_page)
    processed_book_domain = domains.bd.ingestion.ai.processed_book.domain
    canonical_contents, contents_status = processed_book_domain.build_canonical_contents(
        heading_candidates,
        [page.model_dump(mode = "json") for page in candidate.pages],
    )
    if cancel_check is not None and await cancel_check():
        raise asyncio.CancelledError
    _, manifest_key, manifest_sha256 = await domains.bd.ingestion.ai.processed_book.service.publish_manifest(
        candidate,
        canonical_contents,
        contents_status,
        domains.bd.ingestion.ai.versions.CONTENTS_RECONCILER_VERSION,
        _StorageWriter(read_page, write_artifact, artifact_exists),
    )
    return {
        "processed_book_key": manifest_key,
        "processed_book_sha256": manifest_sha256,
        "processed_book_revision": candidate.selection_revision,
        "processed_page_count": len(candidate.pages),
        "contents_status": contents_status,
    }


class _StorageWriter:
    """Adapter allowing shared processed-book persistence to use graph artifact callbacks."""

    def __init__(
        self,
        reader: PageReader,
        writer: ArtifactWriter,
        exists_callback: Callable[[str], Awaitable[bool]],
    ):
        self._reader = reader
        self._writer = writer
        self._exists_callback = exists_callback

    async def exists(self, key: str) -> bool:
        return await self._exists_callback(key)

    async def read_text(self, key: str) -> str:
        return await _read(self._reader, key)

    async def write(self, key: str, content: str, content_type: str = "application/json") -> None:
        result = self._writer(key, content, content_type)
        if inspect.isawaitable(result):
            await result
