"""Bounded content classification over normalized pages and append-only selection artifacts."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
import collections.abc
import typing

import domains

from .... import agents, graphs
from . import domain


logger = logging.getLogger(__name__)
PageReader = collections.abc.Callable[[str], str | None | collections.abc.Awaitable[str | None]]
ArtifactWriter = collections.abc.Callable[[str, str, str], typing.Any]
ArtifactExists = collections.abc.Callable[[str], collections.abc.Awaitable[bool]]


class ContentClassifier(typing.Protocol):
    async def ainvoke(self, input: dict[str, typing.Any], config: dict[str, typing.Any] | None = None) -> dict[str, typing.Any]: ...


def _json_body(value: typing.Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


async def _read_page(reader: PageReader, key: str) -> str:
    value = reader(key)
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, str):
        raise ValueError(f"normalized page {key} could not be read")
    return value


def _excerpt(markdown: str) -> str:
    limit = domains.bd.ingestion.ai.params.CONTENT_CLASSIFICATION_EXCERPT_CHARS
    if len(markdown) <= limit:
        return markdown
    first = int(limit * 0.75)
    return f"{markdown[:first]}\n\n[Middle omitted for bounded page classification]\n\n{markdown[-(limit - first):]}"


async def classify_content(
    state: graphs.ingestion.states.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    artifact_exists: ArtifactExists,
    classifier: ContentClassifier,
    progress_callback = None,
) -> dict[str, typing.Any]:
    """Classify all completed normalized pages; failures and uncertainty keep pages in the Distill set."""
    book_id = state.get("book_id")
    extraction_run_id = state.get("extraction_run_id")
    agentic_run_id = state.get("agentic_run_id")
    page_results = state.get("page_results")
    page_refs = state.get("source_page_refs")
    expected_count = state.get("expected_page_count")
    if not all(isinstance(value, str) for value in (book_id, extraction_run_id, agentic_run_id)):
        raise ValueError("the content classifier requires book, extraction, and ingestion run identities")
    if not isinstance(page_results, list) or len(page_results) != expected_count:
        raise ValueError("content classification requires every normalized page")
    if not isinstance(page_refs, list) or len(page_refs) != expected_count:
        raise ValueError("content classification requires the complete source page list")

    output_key = domains.bd.ingestion.storage.keys.agentic_content_selection_key(book_id, agentic_run_id, 1)
    if await artifact_exists(output_key):
        existing = await _read_page(read_page, output_key)
        saved = domains.bd.ingestion.ai.schemas.ContentSelectionManifest.model_validate_json(existing)
        if (
            saved.book_id != book_id
            or saved.extraction_run_id != extraction_run_id
            or saved.agentic_run_id != agentic_run_id
            or saved.revision != 1
        ):
            raise ValueError("saved content selection does not match the current ingestion run")
        by_page = {page.page_number: page for page in saved.pages}
        for result in page_results:
            selected = by_page[result["page"]]
            result.update(
                content_role = selected.role,
                content_confidence = selected.confidence,
                content_rationale = selected.rationale,
                content_disposition = selected.disposition,
                content_decision_source = selected.decision_source,
            )
        content = _json_body(saved.model_dump(mode = "json"))
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        pointer_key = domains.bd.ingestion.storage.keys.agentic_current_content_selection_key(book_id, agentic_run_id)
        await _write(
            write_artifact,
            pointer_key,
            _json_body({"revision": 1, "key": output_key, "sha256": digest}),
            "application/json",
        )
        return {
            "page_results": page_results,
            "content_selection": [page.model_dump(mode = "json") for page in saved.pages],
            "content_selection_key": output_key,
            "content_selection_sha256": digest,
            "content_selection_revision": saved.revision,
            "selected_page_count": sum(page.disposition == "include" for page in saved.pages),
            "excluded_page_count": sum(page.disposition == "exclude" for page in saved.pages),
        }

    refs_by_page = {ref["page"]: ref for ref in page_refs}
    semaphore = asyncio.Semaphore(domains.bd.ingestion.ai.params.CONTENT_CLASSIFICATION_CONCURRENCY)
    title = str(state.get("book_metadata", {}).get("title") or state.get("source_metadata", {}).get("title") or "")
    run_id = agentic_run_id

    async def classify_one(page_result: dict[str, typing.Any]) -> dict[str, typing.Any]:
        page_number = page_result.get("page")
        if type(page_number) is not int or page_number not in refs_by_page:
            raise ValueError("a normalized page is missing its physical page number")
        source_ref = refs_by_page[page_number]
        await graphs.service.report(
            progress_callback, kind = "content_classification", page = page_number, status = "running",
        )
        status = "failed"
        try:
            markdown = await _read_page(read_page, page_result["normalized_key"])
            expected_hash = page_result.get("normalized_sha256")
            if not isinstance(expected_hash, str) or hashlib.sha256(markdown.encode("utf-8")).hexdigest() != expected_hash:
                raise ValueError(f"normalized page {page_number} failed its integrity check")

            delivery = page_result.get("delivery_disposition", "include")
            audit = page_result.get("assessment", {})
            page_kind = audit.get("page_kind") if isinstance(audit, dict) else None
            if delivery == "discarded_blank":
                role, confidence, rationale = "noise", 1.0, "The ingestion quality audit classified this page as blank."
                decision_source = "content_policy"
                selected = "exclude"
            elif page_kind == "image_only":
                role, confidence, rationale = "unknown", 0.0, "Image-only page retained because its visual content was not classified."
                decision_source = "conservative_fallback"
                selected = "include"
            else:
                payload = {
                    "task": "Classify the page role in the context of this book.",
                    "book_title": title,
                    "physical_page": page_number,
                    "section_path": source_ref.get("section_path", []),
                    "page_markdown_excerpt": _excerpt(markdown),
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
                    assessment = agents.schemas.ContentPageAssessment.model_validate(structured)
                    role = assessment.role.value
                    confidence = assessment.confidence
                    rationale = assessment.rationale
                    selected, decision_source = domain.disposition(
                        role,
                        confidence,
                        delivery_disposition = delivery,
                        has_pictures = source_ref.get("pictures", 0) > 0,
                    )
                except Exception as error:  # noqa: BLE001 — uncertain classifications are retained for review.
                    logger.warning(
                        "[bd-content-classifier] retained page %s after %s",
                        page_number,
                        type(error).__name__,
                    )
                    role, confidence = "unknown", 0.0
                    rationale = "The content classifier was unavailable; this page was kept for Distill and review."
                    selected, decision_source = "include", "conservative_fallback"

            page = domains.bd.ingestion.ai.schemas.ContentSelectionPage(
                page_number = page_number,
                role = role,
                confidence = confidence,
                rationale = rationale,
                disposition = selected,
                decision_source = decision_source,
            )
            page_result.update({
                "content_role": page.role,
                "content_confidence": page.confidence,
                "content_rationale": page.rationale,
                "content_disposition": page.disposition,
                "content_decision_source": page.decision_source,
            })
            status = "done"
            return page.model_dump(mode = "json")
        finally:
            await graphs.service.report(
                progress_callback, kind = "content_classification", page = page_number, status = status,
            )

    async def classify_bounded(page_result: dict[str, typing.Any]) -> dict[str, typing.Any]:
        async with semaphore:
            return await classify_one(page_result)

    rows = await asyncio.gather(*(classify_bounded(page) for page in page_results))
    rows.sort(key = lambda page: page["page_number"])
    selected_count = sum(page["disposition"] == "include" for page in rows)
    excluded_count = len(rows) - selected_count
    if selected_count == 0:
        for page in rows:
            page.update(
                disposition = "include",
                decision_source = "conservative_fallback",
                rationale = "No page met the conservative inclusion threshold; all pages were retained for review.",
            )
        for result in page_results:
            result.update(
                content_disposition = "include",
                content_decision_source = "conservative_fallback",
                content_rationale = "No page met the conservative inclusion threshold; all pages were retained for review.",
            )
        selected_count = len(rows)
        excluded_count = 0

    manifest = domains.bd.ingestion.ai.schemas.ContentSelectionManifest(
        classifier_version = domains.bd.ingestion.ai.versions.CONTENT_CLASSIFIER_VERSION,
        book_id = book_id,
        extraction_run_id = extraction_run_id,
        agentic_run_id = agentic_run_id,
        revision = 1,
        expected_page_count = expected_count,
        pages = rows,
        created_at = time.time(),
    )
    content = _json_body(manifest.model_dump(mode = "json"))
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    await _write(write_artifact, output_key, content, "application/json")
    pointer_key = domains.bd.ingestion.storage.keys.agentic_current_content_selection_key(book_id, agentic_run_id)
    await _write(write_artifact, pointer_key, _json_body({"revision": 1, "key": output_key, "sha256": digest}), "application/json")
    return {
        "page_results": page_results,
        "content_selection": rows,
        "content_selection_key": output_key,
        "content_selection_sha256": digest,
        "content_selection_revision": 1,
        "selected_page_count": selected_count,
        "excluded_page_count": excluded_count,
    }


async def _write(writer: ArtifactWriter, key: str, content: str, content_type: str) -> None:
    value = writer(key, content, content_type)
    if inspect.isawaitable(value):
        await value
