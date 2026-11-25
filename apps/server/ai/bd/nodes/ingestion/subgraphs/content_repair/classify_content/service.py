"""Classify source pages before repair and persist an append-only selection artifact."""
from __future__ import annotations
import domains
from ...... import agents, graphs

import asyncio
import hashlib
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol


logger = logging.getLogger(__name__)
PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]
ArtifactExists = Callable[[str], Awaitable[bool]]


class ContentClassifier(Protocol):
    async def ainvoke(self, input: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]: ...


def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


async def _read_page(reader: PageReader, key: str) -> str:
    value = reader(key)
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, str):
        raise ValueError(f"source page {key} could not be read")
    return value


async def classify_content(
    state: graphs.ingestion.states.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    artifact_exists: ArtifactExists,
    classifier: ContentClassifier,
    progress_callback = None,
) -> dict[str, Any]:
    """Classify audited source pages so excluded pages avoid specialist repair calls."""
    book_id = state.get("book_id")
    extraction_run_id = state.get("extraction_run_id")
    agentic_run_id = state.get("agentic_run_id")
    page_refs = state.get("source_page_refs")
    page_audits = state.get("page_audits")
    expected_count = state.get("expected_page_count")
    if not all(isinstance(value, str) for value in (book_id, extraction_run_id, agentic_run_id)):
        raise ValueError("the content classifier requires book, extraction, and ingestion run identities")
    if not isinstance(page_refs, list) or len(page_refs) != expected_count:
        raise ValueError("content classification requires the complete source page list")
    if not isinstance(page_audits, list) or len(page_audits) != expected_count:
        raise ValueError("content classification requires the complete page audit")

    params = domains.bd.ingestion.ai.content_selection.params
    revision = params.CONTENT_SELECTION_REVISION
    output_key = domains.bd.ingestion.source.storage.keys.agentic_content_selection_key(
        book_id, agentic_run_id, revision,
    )
    if await artifact_exists(output_key):
        existing = await _read_page(read_page, output_key)
        saved = domains.bd.ingestion.ai.content_selection.schemas.ContentSelectionManifest.model_validate_json(existing)
        if (
            saved.book_id != book_id
            or saved.extraction_run_id != extraction_run_id
            or saved.agentic_run_id != agentic_run_id
            or saved.revision != revision
        ):
            raise ValueError("saved content selection does not match the current ingestion run")
        content = _json_body(saved.model_dump(mode = "json"))
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        pointer_key = domains.bd.ingestion.source.storage.keys.agentic_current_content_selection_key(
            book_id, agentic_run_id,
        )
        await _write(
            write_artifact,
            pointer_key,
            _json_body({"revision": revision, "key": output_key, "sha256": digest}),
            "application/json",
        )
        selection_state = _selection_state(saved.pages, output_key, digest, saved.revision)
        await graphs.service.report(
            progress_callback,
            kind = "content_selection",
            classified_pages = [page["page_number"] for page in selection_state["content_selection"]],
            selected_page_count = selection_state["selected_page_count"],
            excluded_page_count = selection_state["excluded_page_count"],
        )
        return selection_state

    audits_by_page = {page["page"]: page for page in page_audits}
    semaphore = asyncio.Semaphore(params.CONTENT_CLASSIFICATION_CONCURRENCY)
    source_metadata = state.get("source_metadata", {})
    title = str(
        source_metadata.get("title") or source_metadata.get("filename") or ""
    ) if isinstance(source_metadata, dict) else ""
    run_id = agentic_run_id

    async def classify_one(source_ref: dict[str, Any]) -> dict[str, Any]:
        page_number = source_ref.get("page")
        if type(page_number) is not int or page_number not in audits_by_page:
            raise ValueError("a source page is missing its physical page number or audit")
        audit = audits_by_page[page_number]
        await graphs.service.report(
            progress_callback, kind = "content_classification", page = page_number, status = "running",
        )
        status = "failed"
        try:
            markdown = await _read_page(read_page, source_ref["key"])
            expected_hash = audit.get("source_sha256")
            if not isinstance(expected_hash, str) or hashlib.sha256(markdown.encode("utf-8")).hexdigest() != expected_hash:
                raise ValueError(f"source page {page_number} failed its audit integrity check")

            assessment = audit.get("assessment", {})
            page_kind = assessment.get("page_kind") if isinstance(assessment, dict) else None
            if page_kind == "blank":
                role = "noise"
                confidence = 1.0
                rationale = "The ingestion quality audit classified this page as blank."
                decision_source = "content_policy"
                selected = "exclude"
            elif domains.bd.ingestion.ai.content_selection.domain.is_source_contents_page(
                markdown,
                source_ref.get("section_path", []),
            ):
                role = "navigation"
                confidence = 1.0
                rationale = (
                    "This page contains the book's printed table of contents; the processed book generates a "
                    "replacement contents tree."
                )
                decision_source = "content_policy"
                selected = "exclude"
            elif page_kind == "image_only":
                role = "unknown"
                confidence = 0.0
                rationale = "Image-only page retained because its visual content was not classified."
                decision_source = "conservative_fallback"
                selected = "include"
            else:
                payload = {
                    "task": "Classify the page role in the context of this book.",
                    "book_title": title,
                    "physical_page": page_number,
                    "section_path": source_ref.get("section_path", []),
                    "page_markdown_excerpt": domains.bd.ingestion.ai.content_selection.domain.excerpt(markdown),
                }
                try:
                    with domains.settings.llm_counter.service.llm_context(
                        ns = "bd", run_id = run_id, node_id = "content_classifier",
                    ):
                        response = await classifier.ainvoke({"messages": [{
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii = False),
                        }]}, config = {
                            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                        })
                    structured = response.get("structured_response") if isinstance(response, dict) else None
                    result = agents.schemas.ContentPageAssessment.model_validate(structured)
                    role = result.role.value
                    confidence = result.confidence
                    rationale = result.rationale
                    selected, decision_source = domains.bd.ingestion.ai.content_selection.domain.disposition(
                        role,
                        confidence,
                        delivery_disposition = "include",
                        has_pictures = source_ref.get("pictures", 0) > 0,
                    )
                except Exception as error:  # noqa: BLE001 — uncertain classifications stay in the book.
                    logger.warning(
                        "[bd-content-classifier] retained page %s after %s",
                        page_number,
                        type(error).__name__,
                    )
                    role = "unknown"
                    confidence = 0.0
                    rationale = "The content classifier was unavailable; this page was kept for Distill and review."
                    selected = "include"
                    decision_source = "conservative_fallback"

            page = domains.bd.ingestion.ai.content_selection.schemas.ContentSelectionPage(
                page_number = page_number,
                role = role,
                confidence = confidence,
                rationale = rationale,
                disposition = selected,
                decision_source = decision_source,
            )
            status = "done"
            return page.model_dump(mode = "json")
        finally:
            await graphs.service.report(
                progress_callback, kind = "content_classification", page = page_number, status = status,
            )

    async def classify_bounded(source_ref: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await classify_one(source_ref)

    rows = await asyncio.gather(*(classify_bounded(page) for page in page_refs))
    rows.sort(key = lambda page: page["page_number"])
    selected_count = sum(page["disposition"] == "include" for page in rows)
    if selected_count == 0:
        fallback_pages = []
        for page in rows:
            assessment = audits_by_page[page["page_number"]].get("assessment")
            if not isinstance(assessment, dict) or assessment.get("page_kind") != "blank":
                fallback_pages.append(page)
        if not fallback_pages:
            raise ValueError(
                "content classification found no nonblank pages to keep after the page audit"
            )
        for page in fallback_pages:
            page.update(
                disposition = "include",
                decision_source = "conservative_fallback",
                rationale = (
                    "Every page was otherwise excluded; this nonblank page was retained to avoid an empty processed "
                    "book. Review the content selection."
                ),
            )

    manifest = domains.bd.ingestion.ai.content_selection.schemas.ContentSelectionManifest(
        classifier_version = domains.bd.ingestion.ai.content_selection.versions.CONTENT_CLASSIFIER_VERSION,
        book_id = book_id,
        extraction_run_id = extraction_run_id,
        agentic_run_id = agentic_run_id,
        revision = revision,
        expected_page_count = expected_count,
        pages = rows,
        created_at = time.time(),
    )
    content = _json_body(manifest.model_dump(mode = "json"))
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    await _write(write_artifact, output_key, content, "application/json")
    pointer_key = domains.bd.ingestion.source.storage.keys.agentic_current_content_selection_key(book_id, agentic_run_id)
    await _write(
        write_artifact,
        pointer_key,
        _json_body({"revision": revision, "key": output_key, "sha256": digest}),
        "application/json",
    )
    selection_state = _selection_state(manifest.pages, output_key, digest, revision)
    await graphs.service.report(
        progress_callback,
        kind = "content_selection",
        classified_pages = [page["page_number"] for page in selection_state["content_selection"]],
        selected_page_count = selection_state["selected_page_count"],
        excluded_page_count = selection_state["excluded_page_count"],
    )
    return selection_state


def _selection_state(pages: list[Any], key: str, digest: str, revision: int) -> dict[str, Any]:
    rows = [page.model_dump(mode = "json") for page in pages]
    selected_count = sum(page["disposition"] == "include" for page in rows)
    return {
        "content_selection": rows,
        "content_selection_key": key,
        "content_selection_sha256": digest,
        "content_selection_revision": revision,
        "selected_page_count": selected_count,
        "excluded_page_count": len(rows) - selected_count,
    }


async def _write(writer: ArtifactWriter, key: str, content: str, content_type: str) -> None:
    value = writer(key, content, content_type)
    if inspect.isawaitable(value):
        await value
