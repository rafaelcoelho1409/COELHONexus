"""Prepare source-linked Distill learning-unit inputs from the pinned book artifacts."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import domains


async def prepare_units(
    storage: Any,
    state: dict[str, Any],
    on_page_progress: Callable[[int, str, int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    source_manifest = await domains.bd.ingestion.source.storage.service.read_manifest(storage, state["book_id"])
    if source_manifest is None or source_manifest.get("status") != domains.bd.ingestion.source.storage.params.STATUS_DONE:
        raise ValueError("Distill requires a completed extraction")
    if source_manifest.get("extraction_id") != state["extraction_run_id"]:
        raise ValueError("the book's current extraction no longer matches this Distill run")
    source_manifest_sha256 = domains.bd.distill.domain.canonical_sha256(source_manifest)
    if source_manifest_sha256 != state["source_manifest_sha256"]:
        raise ValueError("the source manifest changed after this Distill run was queued")

    qa_key = state["ingestion_ai_manifest_key"]
    qa_content = await storage.read_text(qa_key)
    if hashlib.sha256(qa_content.encode("utf-8")).hexdigest() != state["ingestion_ai_manifest_sha256"]:
        raise ValueError("the ingestion QA manifest changed after this Distill run was queued")
    qa_manifest = domains.bd.ingestion.ai.runs.schemas.IngestionAIRunManifest.model_validate_json(qa_content)
    if qa_manifest.status != domains.bd.ingestion.ai.runs.schemas.AgenticRunStatus.DONE:
        raise ValueError("Distill requires a completed ingestion QA run")
    if qa_manifest.quality_status not in {"complete", "complete_with_review"}:
        raise ValueError("the selected ingestion QA run has no publishable quality result")
    if qa_manifest.extraction_run_id != state["extraction_run_id"]:
        raise ValueError("the ingestion QA run is pinned to a different extraction")
    if qa_manifest.expected_page_count != state["expected_page_count"]:
        raise ValueError("the ingestion QA page count does not match the Distill run")

    selection_by_page = None
    processed_structure = None
    if state.get("content_selection_key"):
        selection_content = await storage.read_text(state["content_selection_key"])
        if hashlib.sha256(selection_content.encode("utf-8")).hexdigest() != state["content_selection_sha256"]:
            raise ValueError("the pinned content-selection revision failed its integrity check")
        selection = domains.bd.ingestion.ai.content_selection.schemas.ContentSelectionManifest.model_validate_json(selection_content)
        if (
            selection.book_id != state["book_id"]
            or selection.extraction_run_id != state["extraction_run_id"]
            or selection.agentic_run_id != state["ingestion_ai_run_id"]
            or selection.revision != state["content_selection_revision"]
            or selection.expected_page_count != state["expected_page_count"]
        ):
            raise ValueError("the pinned content-selection revision does not match this Distill run")
        selection_by_page = {page.page_number: page for page in selection.pages}

    if state.get("processed_book_key"):
        processed_content = await storage.read_text(state["processed_book_key"])
        if hashlib.sha256(processed_content.encode("utf-8")).hexdigest() != state["processed_book_sha256"]:
            raise ValueError("the pinned processed-book manifest failed its integrity check")
        processed_book = domains.bd.ingestion.ai.processed_book.schemas.ProcessedBookManifest.model_validate_json(
            processed_content,
        )
        if (
            processed_book.book_id != state["book_id"]
            or processed_book.extraction_run_id != state["extraction_run_id"]
            or processed_book.agentic_run_id != state["ingestion_ai_run_id"]
            or processed_book.selection_revision != state["processed_book_revision"]
            or processed_book.selection_key != state.get("content_selection_key")
            or processed_book.selection_sha256 != state.get("content_selection_sha256")
            or processed_book.source_page_count != state["processed_page_count"]
        ):
            raise ValueError("the processed-book page map does not match this Distill run")
        page_number_map = [
            (item.source_page_number, item.processed_page_number)
            for item in processed_book.pages
        ]
        processed_structure = domains.bd.distill.domain.processed_contents_structure(
            [item.model_dump(mode = "json") for item in processed_book.contents],
            [item.model_dump(mode = "json") for item in processed_book.pages],
        )
    else:
        selected_pages = [
            page for page in qa_manifest.pages
            if (
                (selection_by_page is None or selection_by_page[page.page_number].disposition == "include")
                and page.delivery_disposition != "discarded_blank"
            )
        ]
        page_number_map = [
            (page.page_number, processed_page_number)
            for processed_page_number, page in enumerate(selected_pages, start = 1)
        ]
    if state.get("processed_book_key") and len(page_number_map) != state["selected_page_count"]:
        raise ValueError("the processed page count does not match this Distill run")

    page_by_number = {
        page.get("page"): page
        for page in source_manifest.get("pages", [])
        if isinstance(page, dict) and type(page.get("page")) is int
    }
    qa_by_number = {page.page_number: page for page in qa_manifest.pages}
    source_pages = []
    unit_refs = []
    total_pages = len(page_number_map)
    for source_page_number, processed_page_number in page_number_map:
        if on_page_progress is not None:
            await on_page_progress(source_page_number, "running", total_pages)
        qa_page = qa_by_number.get(source_page_number)
        if qa_page is None:
            raise ValueError(f"the processed book references unknown source page {source_page_number}")
        content_page = selection_by_page.get(qa_page.page_number) if selection_by_page is not None else None
        if selection_by_page is not None and content_page is None:
            raise ValueError(f"content selection has no page {qa_page.page_number}")
        if content_page is not None and content_page.disposition == "exclude":
            if on_page_progress is not None:
                await on_page_progress(source_page_number, "done", total_pages)
            continue
        raw_page = page_by_number.get(qa_page.page_number)
        if raw_page is None:
            raise ValueError(f"source manifest has no page {qa_page.page_number}")
        if processed_structure is not None:
            section_path = processed_structure[0].get(source_page_number, [])
        else:
            section_path = raw_page.get("section_path")
            if not isinstance(section_path, list) or not all(isinstance(item, str) for item in section_path):
                section_path = []
        source_page = {
            "page_number": qa_page.page_number,
            "processed_page_number": processed_page_number,
            "source_key": qa_page.normalized_key,
            "source_sha256": qa_page.normalized_sha256,
            "section_path": section_path,
            "disposition": qa_page.delivery_disposition,
            "review_required": qa_page.status == domains.bd.ingestion.ai.runs.schemas.AgenticPageStatus.REVIEW_REQUIRED,
            "content_role": content_page.role if content_page is not None else "unknown",
            "figures": [
                figure for figure in raw_page.get("figures", [])
                if isinstance(figure, str) and figure
            ][:12],
        }
        source_pages.append(source_page)
        if qa_page.delivery_disposition == "discarded_blank":
            if on_page_progress is not None:
                await on_page_progress(source_page_number, "done", total_pages)
            continue
        page_text = await storage.read_text(qa_page.normalized_key)
        if hashlib.sha256(page_text.encode("utf-8")).hexdigest() != qa_page.normalized_sha256:
            raise ValueError(f"normalized source page {qa_page.page_number} failed its integrity check")
        chunks = domains.bd.distill.domain.split_source_page(
            page_text,
            page_number = qa_page.page_number,
            source_key = qa_page.normalized_key,
            source_sha256 = qa_page.normalized_sha256,
            section_path = section_path,
        )
        for ref, chunk in chunks:
            ref["processed_page_number"] = processed_page_number
            input_key = domains.bd.distill.keys.input_unit_key(state["book_id"], state["run_id"], ref["unit_id"])
            ref["input_key"] = input_key
            input_record = {
                "unit_id": ref["unit_id"],
                "page_number": ref["page_number"],
                "processed_page_number": processed_page_number,
                "section_path": section_path,
                "source_key": qa_page.normalized_key,
                "source_sha256": qa_page.normalized_sha256,
                "text_sha256": ref["text_sha256"],
                "char_start": ref["char_start"],
                "char_end": ref["char_end"],
                "text": chunk,
            }
            if await storage.exists(input_key):
                existing = json.loads(await storage.read_text(input_key))
                if existing.get("text_sha256") != ref["text_sha256"]:
                    raise ValueError("an immutable Distill source-unit key has conflicting content")
            else:
                await storage.write(input_key, domains.bd.distill.domain.json_body(input_record), content_type = domains.bd.distill.params.JSON_TYPE)
            unit_refs.append(ref)
        if on_page_progress is not None:
            await on_page_progress(source_page_number, "done", total_pages)

    return {
        "source_pages": source_pages,
        "unit_refs": unit_refs,
        "source_chapters": (
            processed_structure[1]
            if processed_structure is not None
            else [
                {
                    "title": chapter["title"],
                    "first_page": chapter["first_page"],
                    "last_page": chapter["last_page"],
                }
                for chapter in source_manifest.get("chapters", [])
                if isinstance(chapter, dict)
                and isinstance(chapter.get("title"), str)
                and type(chapter.get("first_page")) is int
                and type(chapter.get("last_page")) is int
                and chapter["first_page"] > 0
                and chapter["last_page"] >= chapter["first_page"]
            ]
        ),
        "title": str(source_manifest.get("title") or source_manifest.get("filename") or "Learning Guide"),
    }


async def extract_unit_batch(
    state: dict[str, Any],
    extract_unit: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Aggregate the per-unit graph results for one bounded fan-out batch."""
    completed = []
    for ref in state.get("unit_batch", []):
        output = await extract_unit({**state, "unit_ref": ref})
        completed.extend(output.get("completed_units", []))
    return {"completed_units": completed}
