"""Persist and validate the canonical, selection-versioned processed book projection."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

import domains
import infra

from . import domain, schemas


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _page_value(page: Any, name: str, default: Any = None) -> Any:
    if isinstance(page, dict):
        return page.get(name, default)
    return getattr(page, name, default)


async def make_candidate(
    source_manifest: dict[str, Any],
    selection: domains.bd.ingestion.ai.schemas.ContentSelectionManifest,
    selection_key: str,
    selection_sha256: str,
    qa_pages: list[Any],
    storage: infra.minio.service.MinIOStorage,
) -> schemas.ProcessedBookCandidate:
    """Build graph input from included, normalized pages and source contents evidence."""
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

    semaphore = asyncio.Semaphore(domains.bd.ingestion.ai.params.PROCESSED_BOOK_READ_CONCURRENCY)

    async def build_page(processed_number: int, selected_page: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        qa_page = qa_by_page[selected_page.page_number]
        status = _page_value(qa_page, "status")
        if status not in {
            domains.bd.ingestion.ai.schemas.AgenticPageStatus.DONE,
            domains.bd.ingestion.ai.schemas.AgenticPageStatus.REVIEW_REQUIRED,
            "done",
            "review_required",
        }:
            raise ValueError(f"selected source page {selected_page.page_number} has no normalized content")
        content_key = _page_value(qa_page, "normalized_key")
        normalized_sha256 = _page_value(qa_page, "normalized_sha256")
        if not isinstance(content_key, str) or not isinstance(normalized_sha256, str):
            raise ValueError(f"selected source page {selected_page.page_number} has incomplete normalized references")
        async with semaphore:
            content = await storage.read_text(content_key)
        content_sha256 = _digest(content)
        if content_sha256 != normalized_sha256:
            raise ValueError(f"normalized source page {selected_page.page_number} failed its integrity check")
        source = source_by_page.get(selected_page.page_number, {})
        source_key = _page_value(qa_page, "source_key") or source.get("key")
        source_sha256 = _page_value(qa_page, "source_sha256") or source.get("source_sha256")
        if not isinstance(source_key, str) or not isinstance(source_sha256, str):
            raise ValueError(f"selected source page {selected_page.page_number} has incomplete raw-source references")
        section_path = source.get("section_path", [])
        if not isinstance(section_path, list) or not all(isinstance(item, str) for item in section_path):
            section_path = []
        page = {
            "processed_page_number": processed_number,
            "source_page_number": selected_page.page_number,
            "source_key": source_key,
            "source_sha256": source_sha256,
            "content_key": content_key,
            "content_sha256": content_sha256,
            "section_path": section_path,
            "role": selected_page.role,
        }
        page_evidence = {
            "source_page_number": selected_page.page_number,
            "evidence": domain.heading_evidence(content, section_path),
        }
        return page, page_evidence

    built_pages = await asyncio.gather(*(
        build_page(processed_number, selected_page)
        for processed_number, selected_page in enumerate(selected, start = 1)
    ))
    pages = [page for page, _ in built_pages]
    evidence = [page_evidence for _, page_evidence in built_pages]
    outline = source_manifest.get("outline", [])
    if not isinstance(outline, list):
        outline = []
    if not outline:
        outline = []
        seen = set()
        for page in evidence:
            for level, title in enumerate(page["evidence"], start = 1):
                title_key = title.casefold().strip()
                if title_key and title_key not in seen:
                    seen.add(title_key)
                    outline.append({
                        "level": min(level, 8),
                        "title": title,
                        "page": page["source_page_number"],
                    })
    return schemas.ProcessedBookCandidate(
        book_id = selection.book_id,
        extraction_run_id = selection.extraction_run_id,
        agentic_run_id = selection.agentic_run_id,
        selection_key = selection_key,
        selection_sha256 = selection_sha256,
        selection_revision = selection.revision,
        expected_source_page_count = selection.expected_page_count,
        pages = pages,
        contents_candidates = domain.contents_candidates(outline, evidence),
        created_at = time.time(),
    )


def candidate_json(candidate: schemas.ProcessedBookCandidate) -> str:
    return _json(candidate.model_dump(mode = "json"))


async def write_candidate(candidate: schemas.ProcessedBookCandidate, storage: infra.minio.service.MinIOStorage) -> str:
    key = domains.bd.ingestion.storage.keys.agentic_processed_book_candidate_key(
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
    pointer_key = domains.bd.ingestion.storage.keys.agentic_processed_book_current_key(book_id, agentic_run_id)
    await storage.write(
        pointer_key,
        _json({"revision": revision, "key": key, "sha256": digest}),
        content_type = "application/json",
    )


async def publish_manifest(
    candidate: schemas.ProcessedBookCandidate,
    contents: list[dict[str, Any]],
    contents_status: str,
    agent_version: str,
    storage: infra.minio.service.MinIOStorage,
    *,
    move_pointer: bool = True,
) -> tuple[domains.bd.ingestion.ai.schemas.ProcessedBookManifest, str, str]:
    """Write the immutable processed-book manifest, then optionally move its current pointer."""
    manifest = domains.bd.ingestion.ai.schemas.ProcessedBookManifest(
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
    key = domains.bd.ingestion.storage.keys.agentic_processed_book_manifest_key(
        candidate.book_id, candidate.agentic_run_id, candidate.selection_revision,
    )
    if await storage.exists(key):
        saved_content = await storage.read_text(key)
        saved = domains.bd.ingestion.ai.schemas.ProcessedBookManifest.model_validate_json(saved_content)
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
) -> tuple[domains.bd.ingestion.ai.schemas.ProcessedBookManifest, str, str] | None:
    """Read the current processed-book revision and verify its pointer and artifact."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    pointer_key = domains.bd.ingestion.storage.keys.agentic_processed_book_current_key(book_id, agentic_run_id)
    if not await storage.exists(pointer_key):
        return None
    pointer = json.loads(await storage.read_text(pointer_key))
    revision = pointer.get("revision") if isinstance(pointer, dict) else None
    key = pointer.get("key") if isinstance(pointer, dict) else None
    digest = pointer.get("sha256") if isinstance(pointer, dict) else None
    expected_key = (
        domains.bd.ingestion.storage.keys.agentic_processed_book_manifest_key(book_id, agentic_run_id, revision)
        if type(revision) is int and revision > 0 else None
    )
    if key != expected_key or not isinstance(digest, str):
        raise ValueError("the current processed-book pointer is malformed")
    content = await storage.read_text(key)
    if _digest(content) != digest:
        raise ValueError("the processed-book manifest failed its integrity check")
    manifest = domains.bd.ingestion.ai.schemas.ProcessedBookManifest.model_validate_json(content)
    if manifest.book_id != book_id or manifest.agentic_run_id != agentic_run_id or manifest.selection_revision != revision:
        raise ValueError("the processed-book manifest does not match its pointer")
    return manifest, key, digest


async def ensure_for_selection(
    book_id: str,
    agentic_run_id: str,
    selection: domains.bd.ingestion.ai.schemas.ContentSelectionManifest,
    selection_key: str,
    selection_sha256: str,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> tuple[domains.bd.ingestion.ai.schemas.ProcessedBookManifest, str, str]:
    """Ensure a reviewed selection has a matching dense map and remapped prior contents."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    current = await read_current(book_id, agentic_run_id, storage = storage)
    if current is not None and current[0].selection_revision == selection.revision:
        if current[0].selection_sha256 != selection_sha256:
            raise ValueError("processed-book selection hash does not match the requested selection")
        return current
    qa_data = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, agentic_run_id, storage = storage)
    if qa_data is None or qa_data.get("status") != "done":
        raise ValueError("a completed ingestion run is required to build the processed book")
    source_manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if source_manifest is None or source_manifest.get("extraction_id") != selection.extraction_run_id:
        raise ValueError("the source extraction does not match the processed-book selection")
    qa_manifest = domains.bd.ingestion.ai.schemas.IngestionAIRunManifest.model_validate(qa_data)
    candidate = await make_candidate(
        source_manifest,
        selection,
        selection_key,
        selection_sha256,
        qa_manifest.pages,
        storage,
    )
    if current is not None:
        prior_entries = [item.model_dump(mode = "json") for item in current[0].contents]
        prior_status = current[0].contents_status
        agent_version = current[0].contents_agent_version
    else:
        prior_entries = []
        outline = source_manifest.get("outline", [])
        if isinstance(outline, list):
            prior_entries = [{
                "level": item.get("level", 1),
                "title": item.get("title", ""),
                "source_page_number": item.get("page"),
                "confidence": 0.25,
                "review_required": True,
            } for item in outline if isinstance(item, dict)]
        prior_status = "needs_review"
        agent_version = "legacy-source-outline"
    mapped_contents, contents_status = domain.remap_contents(
        prior_entries, [page.model_dump(mode = "json") for page in candidate.pages],
    )
    if prior_status != "complete":
        contents_status = "needs_review" if mapped_contents else "unavailable"
    return await publish_manifest(
        candidate, mapped_contents, contents_status, agent_version, storage, move_pointer = False,
    )
