"""Build a versioned candidate page set from the completed QA and selection passes."""
from __future__ import annotations
import domains
from .... import graphs

import hashlib
import inspect
from collections.abc import Awaitable, Callable
from typing import Any


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]


async def materialize_processed_book(
    state: graphs.ingestion.states.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    artifact_exists: Callable[[str], Awaitable[bool]],
) -> dict[str, Any]:
    """Persist one immutable candidate manifest with dense processed numbers and original source references."""
    selection_key = state.get("content_selection_key")
    selection_sha256 = state.get("content_selection_sha256")
    selection_revision = state.get("content_selection_revision")
    if not isinstance(selection_key, str) or not isinstance(selection_sha256, str):
        raise ValueError("processed-book materialization requires a completed page selection")
    if type(selection_revision) is not int or selection_revision < 1:
        raise ValueError("processed-book materialization requires a valid selection revision")
    candidate_key = domains.bd.ingestion.storage.keys.agentic_processed_book_candidate_key(
        state["book_id"], state["agentic_run_id"], selection_revision,
    )
    if await artifact_exists(candidate_key):
        current = read_page(candidate_key)
        if inspect.isawaitable(current):
            current = await current
        if not isinstance(current, str):
            raise ValueError("the saved processed-book candidate could not be read")
        candidate = domains.bd.ingestion.ai.processed_book.schemas.ProcessedBookCandidate.model_validate_json(current)
        if (
            candidate.book_id != state["book_id"]
            or candidate.extraction_run_id != state["extraction_run_id"]
            or candidate.agentic_run_id != state["agentic_run_id"]
            or candidate.selection_revision != selection_revision
            or candidate.selection_sha256 != selection_sha256
        ):
            raise ValueError("the saved processed-book candidate does not match this graph run")
        return {
            "processed_book_candidate_key": candidate_key,
            "processed_page_count": len(candidate.pages),
            "processed_book_revision": selection_revision,
        }

    selection_content = read_page(selection_key)
    if inspect.isawaitable(selection_content):
        selection_content = await selection_content
    if not isinstance(selection_content, str):
        raise ValueError("the content-selection artifact could not be read")
    if hashlib.sha256(selection_content.encode("utf-8")).hexdigest() != selection_sha256:
        raise ValueError("the content-selection artifact failed its integrity check")
    selection = domains.bd.ingestion.ai.schemas.ContentSelectionManifest.model_validate_json(selection_content)
    if selection.revision != selection_revision:
        raise ValueError("the content-selection revision does not match the graph state")

    page_refs = {page["page"]: page for page in state["source_page_refs"]}
    qa_pages = []
    for result in state["page_results"]:
        reference = page_refs.get(result["page"], {})
        qa_pages.append({
            **result,
            "page_number": result["page"],
            "source_key": reference.get("key"),
            "source_sha256": result.get("source_sha256"),
        })
    source_manifest = {
        "pages": state["source_page_refs"],
        "outline": state.get("source_outline", []),
    }
    candidate = await domains.bd.ingestion.ai.processed_book.service.make_candidate(
        source_manifest,
        selection,
        selection_key,
        selection_sha256,
        qa_pages,
        _StorageReader(read_page),
    )
    candidate_content = domains.bd.ingestion.ai.processed_book.service.candidate_json(candidate)
    write_result = write_artifact(candidate_key, candidate_content, "application/json")
    if inspect.isawaitable(write_result):
        await write_result
    return {
        "processed_book_candidate_key": candidate_key,
        "processed_page_count": len(candidate.pages),
        "processed_book_revision": selection_revision,
    }


class _StorageReader:
    """Minimal adapter for the processed-book builder's hash-checked text reads."""

    def __init__(self, reader: PageReader):
        self._reader = reader

    async def read_text(self, key: str) -> str:
        value = self._reader(key)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, str):
            raise ValueError(f"processed-book content {key} could not be read")
        return value
