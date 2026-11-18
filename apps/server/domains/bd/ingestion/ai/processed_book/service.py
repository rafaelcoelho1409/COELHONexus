"""Persist and validate the canonical, selection-versioned processed book projection."""
from __future__ import annotations
import infra
from . import domain, params, schemas
from .. import content_selection, runs, versions
from ... import source

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]
ArtifactExists = Callable[[str], Awaitable[bool]]
CancelCheck = Callable[[], Awaitable[bool]]
PageProgressReporter = Callable[[int, int, int], Awaitable[None]]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _page_value(page: Any, name: str, default: Any = None) -> Any:
    if isinstance(page, dict):
        return page.get(name, default)
    return getattr(page, name, default)


async def _read_callback(reader: PageReader, key: str) -> str:
    value = reader(key)
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, str):
        raise ValueError(f"processed-book artifact {key} could not be read")
    return value


async def materialize_processed_book(
    state: Mapping[str, Any],
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    artifact_exists: ArtifactExists,
    progress_callback: PageProgressReporter | None = None,
) -> dict[str, Any]:
    """Persist one immutable candidate page set with dense numbers and raw-source references."""
    selection_key = state.get("content_selection_key")
    selection_sha256 = state.get("content_selection_sha256")
    selection_revision = state.get("content_selection_revision")
    if not isinstance(selection_key, str) or not isinstance(selection_sha256, str):
        raise ValueError("processed-book materialization requires a completed page selection")
    if type(selection_revision) is not int or selection_revision < 1:
        raise ValueError("processed-book materialization requires a valid selection revision")

    book_id = state.get("book_id")
    agentic_run_id = state.get("agentic_run_id")
    extraction_run_id = state.get("extraction_run_id")
    if not all(isinstance(value, str) for value in (book_id, agentic_run_id, extraction_run_id)):
        raise ValueError("processed-book materialization requires complete run identities")
    candidate_key = source.storage.keys.agentic_processed_book_candidate_key(
        book_id,
        agentic_run_id,
        selection_revision,
    )
    if await artifact_exists(candidate_key):
        current = await _read_callback(read_page, candidate_key)
        candidate = schemas.ProcessedBookCandidate.model_validate_json(current)
        if (
            candidate.book_id != book_id
            or candidate.extraction_run_id != extraction_run_id
            or candidate.agentic_run_id != agentic_run_id
            or candidate.selection_revision != selection_revision
            or candidate.selection_sha256 != selection_sha256
        ):
            raise ValueError("the saved processed-book candidate does not match this graph run")
        if progress_callback is not None:
            await progress_callback(len(candidate.pages), len(candidate.pages), 0)
        return {
            "processed_book_candidate_key": candidate_key,
            "processed_page_count": len(candidate.pages),
            "processed_book_revision": selection_revision,
        }

    selection_content = await _read_callback(read_page, selection_key)
    if _digest(selection_content) != selection_sha256:
        raise ValueError("the content-selection artifact failed its integrity check")
    selection = content_selection.schemas.ContentSelectionManifest.model_validate_json(selection_content)
    if selection.revision != selection_revision:
        raise ValueError("the content-selection revision does not match the graph state")

    source_page_refs = state.get("source_page_refs")
    page_results = state.get("page_results")
    if not isinstance(source_page_refs, list) or not isinstance(page_results, list):
        raise ValueError("processed-book materialization requires complete source and repaired page lists")
    page_refs = {
        page["page"]: page
        for page in source_page_refs
        if isinstance(page, dict) and type(page.get("page")) is int
    }
    qa_pages = []
    for result in page_results:
        if not isinstance(result, dict) or type(result.get("page")) is not int:
            raise ValueError("processed-book materialization received a malformed page result")
        reference = page_refs.get(result["page"], {})
        qa_pages.append({
            **result,
            "page_number": result["page"],
            "source_key": reference.get("key"),
            "source_sha256": result.get("source_sha256"),
        })

    candidate = await make_candidate(
        {"pages": source_page_refs, "book_id": book_id, "extraction_id": extraction_run_id},
        selection,
        selection_key,
        selection_sha256,
        qa_pages,
        _CallbackReader(read_page),
        progress_callback,
    )
    content = candidate_json(candidate)
    result = write_artifact(candidate_key, content, "application/json")
    if inspect.isawaitable(result):
        await result
    return {
        "processed_book_candidate_key": candidate_key,
        "processed_page_count": len(candidate.pages),
        "processed_book_revision": selection_revision,
    }


async def _candidate_headings(
    candidate: schemas.ProcessedBookCandidate,
    read_page: PageReader,
    progress_callback: PageProgressReporter | None = None,
) -> list[dict[str, Any]]:
    """Read selected-page headings, rebuilding legacy immutable candidates when needed."""
    if candidate.heading_candidates_built:
        return [item.model_dump(mode="json") for item in candidate.heading_candidates]

    semaphore = asyncio.Semaphore(params.PAGE_READ_CONCURRENCY)
    total = len(candidate.pages)
    completed = 0
    active = 0
    progress_lock = asyncio.Lock()

    async def update_progress(*, active_delta: int, completed_delta: int = 0) -> None:
        nonlocal active, completed
        async with progress_lock:
            next_active = active + active_delta
            next_completed = completed + completed_delta
            if progress_callback is not None:
                await progress_callback(next_completed, total, next_active)
            active = next_active
            completed = next_completed

    if progress_callback is not None:
        await progress_callback(0, total, 0)

    async def page_evidence_for(page: Any) -> dict[str, Any]:
        async with semaphore:
            started = False
            succeeded = False
            try:
                await update_progress(active_delta = 1)
                started = True
                content = await _read_callback(read_page, page.content_key)
                result = {
                    "source_page_number": page.source_page_number,
                    "section_path": page.section_path,
                    "role": page.role,
                    "headings": domain.markdown_headings(content),
                    "evidence": domain.heading_evidence(content, page.section_path),
                }
                succeeded = True
                return result
            finally:
                if started:
                    await update_progress(
                        active_delta = -1,
                        completed_delta = 1 if succeeded else 0,
                    )

    page_evidence = await asyncio.gather(*(page_evidence_for(page) for page in candidate.pages))
    return domain.canonical_heading_candidates(page_evidence)


async def reconcile_contents(
    state: Mapping[str, Any],
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    artifact_exists: ArtifactExists,
    cancel_check: CancelCheck | None = None,
    progress_callback: PageProgressReporter | None = None,
) -> dict[str, Any]:
    """Build canonical contents from selected-page headings and publish the processed-book manifest."""
    candidate_key = state.get("processed_book_candidate_key")
    if not isinstance(candidate_key, str):
        raise ValueError("contents generation requires a materialized processed-book candidate")
    candidate_content = await _read_callback(read_page, candidate_key)
    candidate = schemas.ProcessedBookCandidate.model_validate_json(candidate_content)
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
    heading_candidates = await _candidate_headings(candidate, read_page, progress_callback)
    contents, contents_status = domain.build_canonical_contents(
        heading_candidates,
        [page.model_dump(mode="json") for page in candidate.pages],
    )
    if cancel_check is not None and await cancel_check():
        raise asyncio.CancelledError
    _, manifest_key, manifest_sha256 = await publish_manifest(
        candidate,
        contents,
        contents_status,
        versions.CONTENTS_RECONCILER_VERSION,
        _CallbackWriter(read_page, write_artifact, artifact_exists),
    )
    return {
        "processed_book_key": manifest_key,
        "processed_book_sha256": manifest_sha256,
        "processed_book_revision": candidate.selection_revision,
        "processed_page_count": len(candidate.pages),
        "contents_status": contents_status,
    }


class _CallbackReader:
    """Adapt the graph's artifact callback to the processed-book storage read interface."""

    def __init__(self, reader: PageReader):
        self._reader = reader

    async def read_text(self, key: str) -> str:
        return await _read_callback(self._reader, key)


class _CallbackWriter(_CallbackReader):
    """Adapt graph artifact callbacks to processed-book manifest persistence."""

    def __init__(
        self,
        reader: PageReader,
        writer: ArtifactWriter,
        exists_callback: ArtifactExists,
    ):
        super().__init__(reader)
        self._writer = writer
        self._exists_callback = exists_callback

    async def exists(self, key: str) -> bool:
        return await self._exists_callback(key)

    async def write(self, key: str, content: str, content_type: str = "application/json") -> None:
        result = self._writer(key, content, content_type)
        if inspect.isawaitable(result):
            await result


async def make_candidate(
    source_manifest: dict[str, Any],
    selection: content_selection.schemas.ContentSelectionManifest,
    selection_key: str,
    selection_sha256: str,
    qa_pages: list[Any],
    storage: infra.minio.service.MinIOStorage,
    progress_callback: PageProgressReporter | None = None,
) -> schemas.ProcessedBookCandidate:
    """Build graph input from included, visually enriched pages and source contents evidence."""
    if (
        selection.book_id != source_manifest.get("book_id", selection.book_id)
        or selection.extraction_run_id != source_manifest.get("extraction_id", selection.extraction_run_id)
        or selection.expected_page_count != len(source_manifest.get("pages", []))
    ):
        raise ValueError("the content selection does not match the source extraction")

    qa_by_page = {
        _page_value(page, "page_number", _page_value(page, "page")): page
        for page in qa_pages
    }
    included = [page for page in selection.pages if page.disposition == "include"]
    missing_qa_pages = [page.page_number for page in included if page.page_number not in qa_by_page]
    if missing_qa_pages:
        raise ValueError(f"selected source pages have no ingestion QA result: {missing_qa_pages[:10]}")

    source_by_page = {
        page.get("page"): page for page in source_manifest.get("pages", [])
        if isinstance(page, dict) and type(page.get("page")) is int
    }
    selected = [
        page for page in selection.pages
        if page.disposition == "include"
        and (
            _page_value(qa_by_page[page.page_number], "delivery_disposition", "include")
        ) != "discarded_blank"
    ]
    if not selected:
        raise ValueError("the processed book must retain at least one nonblank page")

    total = len(selected)
    completed = 0
    active = 0
    progress_lock = asyncio.Lock()

    async def update_progress(*, active_delta: int, completed_delta: int = 0) -> None:
        nonlocal active, completed
        async with progress_lock:
            next_active = active + active_delta
            next_completed = completed + completed_delta
            if progress_callback is not None:
                await progress_callback(next_completed, total, next_active)
            active = next_active
            completed = next_completed

    if progress_callback is not None:
        await progress_callback(0, total, 0)

    semaphore = asyncio.Semaphore(params.PAGE_READ_CONCURRENCY)

    async def build_page(processed_number: int, selected_page: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        qa_page = qa_by_page[selected_page.page_number]
        status = _page_value(qa_page, "status")
        if status not in {
            runs.schemas.AgenticPageStatus.DONE,
            runs.schemas.AgenticPageStatus.REVIEW_REQUIRED,
            "done",
            "review_required",
        }:
            raise ValueError(f"selected source page {selected_page.page_number} has no normalized content")
        content_key = _page_value(qa_page, "enriched_key") or _page_value(qa_page, "normalized_key")
        content_sha256 = _page_value(qa_page, "enriched_sha256") or _page_value(qa_page, "normalized_sha256")
        if not isinstance(content_key, str) or not isinstance(content_sha256, str):
            raise ValueError(f"selected source page {selected_page.page_number} has incomplete processed references")
        source = source_by_page.get(selected_page.page_number, {})
        source_key = _page_value(qa_page, "source_key") or source.get("key")
        source_sha256 = _page_value(qa_page, "source_sha256") or source.get("source_sha256")
        if not isinstance(source_key, str) or not isinstance(source_sha256, str):
            raise ValueError(f"selected source page {selected_page.page_number} has incomplete raw-source references")
        async with semaphore:
            started = False
            succeeded = False
            try:
                await update_progress(active_delta = 1)
                started = True
                content = await storage.read_text(content_key)
                actual_sha256 = _digest(content)
                if actual_sha256 != content_sha256:
                    raise ValueError(
                        f"processed source page {selected_page.page_number} failed its integrity check"
                    )
                section_path = source.get("section_path", [])
                if not isinstance(section_path, list) or not all(isinstance(item, str) for item in section_path):
                    section_path = []
                page = {
                    "processed_page_number": processed_number,
                    "source_page_number": selected_page.page_number,
                    "source_key": source_key,
                    "source_sha256": source_sha256,
                    "content_key": content_key,
                    "content_sha256": actual_sha256,
                    "section_path": section_path,
                    "role": selected_page.role,
                }
                page_evidence = {
                    "source_page_number": selected_page.page_number,
                    "evidence": domain.heading_evidence(content, section_path),
                    "headings": domain.markdown_headings(content),
                    "section_path": section_path,
                    "role": selected_page.role,
                }
                succeeded = True
                return page, page_evidence
            finally:
                if started:
                    await update_progress(
                        active_delta = -1,
                        completed_delta = 1 if succeeded else 0,
                    )

    built_pages = await asyncio.gather(*(
        build_page(processed_number, selected_page)
        for processed_number, selected_page in enumerate(selected, start = 1)
    ))
    pages = [page for page, _ in built_pages]
    evidence = [page_evidence for _, page_evidence in built_pages]
    return schemas.ProcessedBookCandidate(
        book_id = selection.book_id,
        extraction_run_id = selection.extraction_run_id,
        agentic_run_id = selection.agentic_run_id,
        selection_key = selection_key,
        selection_sha256 = selection_sha256,
        selection_revision = selection.revision,
        expected_source_page_count = selection.expected_page_count,
        pages = pages,
        heading_candidates = domain.canonical_heading_candidates(evidence),
        heading_candidates_built = True,
        created_at = time.time(),
    )


def candidate_json(candidate: schemas.ProcessedBookCandidate) -> str:
    return _json(candidate.model_dump(mode = "json"))


async def write_candidate(
    candidate: schemas.ProcessedBookCandidate,
    storage: infra.minio.service.MinIOStorage,
) -> str:
    key = source.storage.keys.agentic_processed_book_candidate_key(
        candidate.book_id, candidate.agentic_run_id, candidate.selection_revision,
    )
    content = candidate_json(candidate)
    if await storage.exists(key):
        if await storage.read_text(key) != content:
            raise ValueError("an immutable processed-book candidate already exists with different content")
    else:
        await storage.write(key, content, content_type = "application/json")
    return key


async def write_current_pointer(
    book_id: str,
    agentic_run_id: str,
    revision: int,
    key: str,
    digest: str,
    storage: infra.minio.service.MinIOStorage,
) -> None:
    pointer_key = source.storage.keys.agentic_processed_book_current_key(book_id, agentic_run_id)
    await storage.write(
        pointer_key,
        _json({"revision": revision, "key": key, "sha256": digest}),
        content_type = "application/json",
    )


async def clear_manifest_for_replay(
    book_id: str,
    agentic_run_id: str,
    revision: int,
    storage: infra.minio.service.MinIOStorage,
) -> None:
    """Remove only the reconciled manifest for a replay, preserving its candidate input."""
    if type(revision) is not int or revision < 1:
        raise ValueError("processed-book revision must be a positive integer")

    manifest_key = source.storage.keys.agentic_processed_book_manifest_key(
        book_id,
        agentic_run_id,
        revision,
    )
    pointer_key = source.storage.keys.agentic_processed_book_current_key(
        book_id,
        agentic_run_id,
    )
    await storage.delete(manifest_key)

    if not await storage.exists(pointer_key):
        return
    pointer = json.loads(await storage.read_text(pointer_key))
    if isinstance(pointer, dict) and pointer.get("key") == manifest_key:
        await storage.delete(pointer_key)


async def publish_manifest(
    candidate: schemas.ProcessedBookCandidate,
    contents: list[dict[str, Any]],
    contents_status: str,
    agent_version: str,
    storage: infra.minio.service.MinIOStorage,
    *,
    move_pointer: bool = True,
) -> tuple[schemas.ProcessedBookManifest, str, str]:
    """Write the immutable processed-book manifest, then optionally move its current pointer."""
    manifest = schemas.ProcessedBookManifest(
        book_id = candidate.book_id,
        extraction_run_id = candidate.extraction_run_id,
        agentic_run_id = candidate.agentic_run_id,
        selection_key = candidate.selection_key,
        selection_sha256 = candidate.selection_sha256,
        selection_revision = candidate.selection_revision,
        expected_source_page_count = candidate.expected_source_page_count,
        source_page_count = len(candidate.pages),
        pages = candidate.pages,
        contents = contents,
        contents_status = contents_status,
        contents_agent_version = agent_version,
        created_at = time.time(),
    )
    content = _json(manifest.model_dump(mode = "json"))
    digest = _digest(content)
    key = source.storage.keys.agentic_processed_book_manifest_key(
        candidate.book_id, candidate.agentic_run_id, candidate.selection_revision,
    )
    if await storage.exists(key):
        saved_content = await storage.read_text(key)
        saved = schemas.ProcessedBookManifest.model_validate_json(saved_content)
        expected_pages = [page.model_dump(mode = "json") for page in candidate.pages]
        if (
            saved.selection_key != candidate.selection_key
            or saved.selection_sha256 != candidate.selection_sha256
            or [page.model_dump(mode = "json") for page in saved.pages] != expected_pages
            or [item.model_dump(mode = "json") for item in saved.contents] != contents
            or saved.contents_status != contents_status
        ):
            raise ValueError("an immutable processed-book revision already exists with different content")
        manifest = saved
        content = saved_content
        digest = _digest(content)
    else:
        await storage.write(key, content, content_type = "application/json")
    if move_pointer:
        await write_current_pointer(
            candidate.book_id, candidate.agentic_run_id, candidate.selection_revision, key, digest, storage,
        )
    return manifest, key, digest


async def read_current(
    book_id: str,
    agentic_run_id: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[schemas.ProcessedBookManifest, str, str] | None:
    """Read the current processed-book revision and verify its pointer and artifact."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    pointer_key = source.storage.keys.agentic_processed_book_current_key(book_id, agentic_run_id)
    if not await storage.exists(pointer_key):
        return None
    pointer = json.loads(await storage.read_text(pointer_key))
    revision = pointer.get("revision") if isinstance(pointer, dict) else None
    key = pointer.get("key") if isinstance(pointer, dict) else None
    digest = pointer.get("sha256") if isinstance(pointer, dict) else None
    expected_key = (
        source.storage.keys.agentic_processed_book_manifest_key(book_id, agentic_run_id, revision)
        if type(revision) is int and revision > 0 else None
    )
    if key != expected_key or not isinstance(digest, str):
        raise ValueError("the current processed-book pointer is malformed")
    content = await storage.read_text(key)
    if _digest(content) != digest:
        raise ValueError("the processed-book manifest failed its integrity check")
    manifest = schemas.ProcessedBookManifest.model_validate_json(content)
    if manifest.book_id != book_id or manifest.agentic_run_id != agentic_run_id or manifest.selection_revision != revision:
        raise ValueError("the processed-book manifest does not match its pointer")
    return manifest, key, digest


async def ensure_for_selection(
    book_id: str,
    agentic_run_id: str,
    selection: content_selection.schemas.ContentSelectionManifest,
    selection_key: str,
    selection_sha256: str,
    *,
    ingestion_ai_run: dict[str, Any],
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[schemas.ProcessedBookManifest, str, str]:
    """Ensure a reviewed selection has a matching dense map and remapped prior contents."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    current = await read_current(book_id, agentic_run_id, storage = storage)
    if current is not None and current[0].selection_revision == selection.revision:
        if current[0].selection_sha256 != selection_sha256:
            raise ValueError("processed-book selection hash does not match the requested selection")
        return current
    if (
        ingestion_ai_run.get("book_id") != book_id
        or ingestion_ai_run.get("agentic_run_id") != agentic_run_id
    ):
        raise ValueError("the ingestion run does not match the requested processed book")
    if ingestion_ai_run.get("status") != "done":
        raise ValueError("a completed ingestion run is required to build the processed book")
    source_manifest = await source.storage.service.read_manifest(storage, book_id)
    if source_manifest is None or source_manifest.get("extraction_id") != selection.extraction_run_id:
        raise ValueError("the source extraction does not match the processed-book selection")
    qa_manifest = runs.schemas.IngestionAIRunManifest.model_validate(ingestion_ai_run)
    candidate = await make_candidate(
        source_manifest,
        selection,
        selection_key,
        selection_sha256,
        qa_manifest.pages,
        storage,
    )
    canonical_contents, contents_status = domain.build_canonical_contents(
        [item.model_dump(mode = "json") for item in candidate.heading_candidates],
        [page.model_dump(mode = "json") for page in candidate.pages],
    )
    return await publish_manifest(
        candidate,
        canonical_contents,
        contents_status,
        versions.CONTENTS_RECONCILER_VERSION,
        storage,
        move_pointer = False,
    )
