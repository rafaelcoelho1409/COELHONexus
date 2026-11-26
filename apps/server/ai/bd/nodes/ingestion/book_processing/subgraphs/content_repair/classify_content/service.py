"""Classify source pages before repair and persist an append-only selection artifact."""
from __future__ import annotations
import domains
from ....... import agents, graphs

import asyncio
import hashlib
import inspect
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from pydantic import ValidationError


logger = logging.getLogger(__name__)
PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]
ArtifactExists = Callable[[str], Awaitable[bool]]


class ContentClassifier(Protocol):
    async def ainvoke(self, input: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]: ...


def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


def _classification_error_summary(error: Exception) -> str:
    if not isinstance(error, ValidationError):
        return type(error).__name__
    issues = error.errors(include_input = False, include_context = False)
    return "; ".join(
        f"{'.'.join(str(part) for part in issue.get('loc', ())) or 'response'} "
        f"({issue.get('type', 'validation_error')})"
        for issue in issues[:5]
    ) or "response did not match schema"


def _response_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            mapped = dump(mode = "python")
        except TypeError:
            mapped = dump()
        return dict(mapped) if isinstance(mapped, Mapping) else None
    return None


def _json_objects(content: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    values = []
    for start, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _classification_candidates(response: Any) -> list[dict[str, Any]]:
    """Extract only schema-shaped data from common LangChain agent response envelopes."""
    required_fields = {"role", "confidence", "rationale"}
    candidates: list[dict[str, Any]] = []
    queue = [response]
    seen: set[int] = set()

    while queue and len(seen) < 128:
        value = queue.pop(0)
        if value is None or id(value) in seen:
            continue
        seen.add(id(value))
        mapped = _response_mapping(value)
        if mapped is not None:
            if required_fields.issubset(mapped):
                candidates.append(mapped)
            for key in ("structured_response", "output", "result", "data", "args", "arguments"):
                nested = mapped.get(key)
                if isinstance(nested, str):
                    queue.extend(_json_objects(nested))
                elif isinstance(nested, (Mapping, list, tuple)) or callable(getattr(nested, "model_dump", None)):
                    queue.append(nested)
            messages = mapped.get("messages")
            if isinstance(messages, list):
                queue.extend(messages)
            tool_calls = mapped.get("tool_calls")
            if isinstance(tool_calls, list):
                queue.extend(tool_calls)
            additional = mapped.get("additional_kwargs")
            additional_mapping = _response_mapping(additional)
            if additional_mapping is not None:
                queue.append(additional_mapping)
        elif isinstance(value, (list, tuple)):
            queue.extend(value)
        elif isinstance(value, str):
            queue.extend(_json_objects(value))

        message = mapped or {}
        tool_calls = message.get("tool_calls") or getattr(value, "tool_calls", None)
        if isinstance(tool_calls, list):
            for call in tool_calls:
                call_mapping = _response_mapping(call) or {}
                function_mapping = _response_mapping(call_mapping.get("function")) or {}
                name = call_mapping.get("name") or function_mapping.get("name")
                if name != "ContentPageAssessment":
                    continue
                arguments = call_mapping.get("args") or function_mapping.get("arguments")
                if isinstance(arguments, str):
                    queue.extend(_json_objects(arguments))
                elif arguments is not None:
                    queue.append(arguments)

        role = message.get("role") or message.get("type") or getattr(value, "type", None)
        if role in {"assistant", "ai", "AIMessage"}:
            content = message.get("content") if message else getattr(value, "content", None)
            if isinstance(content, str):
                queue.extend(_json_objects(content))
            elif isinstance(content, list):
                for block in content:
                    block_mapping = _response_mapping(block)
                    if block_mapping is not None and isinstance(block_mapping.get("text"), str):
                        queue.extend(_json_objects(block_mapping["text"]))

    return candidates


def _validated_classification(response: Any) -> agents.schemas.ContentPageAssessment:
    last_error = None
    for candidate in _classification_candidates(response):
        try:
            return agents.schemas.ContentPageAssessment.model_validate(candidate)
        except ValidationError as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise ValueError("the content classifier returned no structured assessment")


def _safe_model_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    label = re.sub(r"[^A-Za-z0-9._:/-]", "_", value.strip()[:120])
    return label or None


def _response_model_metadata(response: Any) -> tuple[str | None, str | None]:
    response_mapping = _response_mapping(response) or {}
    messages = response_mapping.get("messages")
    candidates = list(reversed(messages)) if isinstance(messages, list) else [response]
    for message in candidates:
        message_mapping = _response_mapping(message) or {}
        metadata = _response_mapping(
            message_mapping.get("response_metadata") or getattr(message, "response_metadata", None),
        ) or {}
        model = next((metadata.get(key) for key in ("model_name", "model", "ls_model_name") if metadata.get(key)), None)
        provider = next((metadata.get(key) for key in ("ls_provider", "provider", "model_provider") if metadata.get(key)), None)
        if model or provider:
            return _safe_model_label(provider), _safe_model_label(model)
    return None, None


def _response_shape_summary(response: Any) -> str:
    mapped = _response_mapping(response)
    if mapped is None:
        return f"type={type(response).__name__}"
    structured = _response_mapping(mapped.get("structured_response"))
    messages = mapped.get("messages")
    message_summary = "messages=none"
    if isinstance(messages, list):
        for message in reversed(messages):
            message_mapping = _response_mapping(message) or {}
            role = message_mapping.get("role") or message_mapping.get("type") or getattr(message, "type", None)
            if role in {"assistant", "ai", "AIMessage"}:
                content = message_mapping.get("content") or getattr(message, "content", None)
                calls = message_mapping.get("tool_calls") or getattr(message, "tool_calls", None)
                tool_names = sorted({
                    call_mapping["name"]
                    for call in calls
                    if isinstance(calls, list)
                    and (call_mapping := _response_mapping(call)) is not None
                    and isinstance(call_mapping.get("name"), str)
                }) if isinstance(calls, list) else []
                message_summary = (
                    f"assistant_content={type(content).__name__}; tool_names={tool_names[:4]}"
                )
                break
    structured_keys = sorted(structured)[:12] if structured else []
    return (
        f"type={type(response).__name__}; keys={sorted(mapped)[:12]}; "
        f"structured_keys={structured_keys}; {message_summary}"
    )[:700]


async def _read_page(reader: PageReader, key: str) -> str:
    value = reader(key)
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, str):
        raise TypeError(f"source page {key} could not be read")
    return value


async def classify_content(
    state: graphs.ingestion.book_processing.states.IngestionRunState,
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
        classifier_provider = None
        classifier_model = None
        classification_error = None
        classifier_response_shape = None
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
                classification_error = "image_only_page_not_classified"
            else:
                payload = {
                    "task": "Classify the page role in the context of this book.",
                    "book_title": title,
                    "physical_page": page_number,
                    "section_path": source_ref.get("section_path", []),
                    "page_markdown_excerpt": domains.bd.ingestion.ai.content_selection.domain.excerpt(markdown),
                }
                response = None
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
                    classifier_provider, classifier_model = _response_model_metadata(response)
                    classifier_response_shape = _response_shape_summary(response)
                    result = _validated_classification(response)
                    role = result.role.value
                    confidence = result.confidence
                    rationale = result.rationale
                    selected, decision_source = domains.bd.ingestion.ai.content_selection.domain.disposition(
                        role,
                        confidence,
                        delivery_disposition = "include",
                        has_pictures = source_ref.get("pictures", 0) > 0,
                    )
                except Exception as error:
                    classifier_response_shape = classifier_response_shape or _response_shape_summary(response)
                    classification_error = _classification_error_summary(error)
                    logger.warning(
                        "[bd-content-classifier] retained page %s after %s: %s "
                        "provider=%s model=%s response=%s",
                        page_number,
                        type(error).__name__,
                        classification_error,
                        classifier_provider or "unknown",
                        classifier_model or "unknown",
                        classifier_response_shape,
                    )
                    role = "unknown"
                    confidence = 0.0
                    rationale = (
                        "The content classifier did not return a valid structured result; this page was kept "
                        "conservatively for Distill and review."
                    )
                    selected = "include"
                    decision_source = "conservative_fallback"

            page = domains.bd.ingestion.ai.content_selection.schemas.ContentSelectionPage(
                page_number = page_number,
                role = role,
                confidence = confidence,
                rationale = rationale,
                disposition = selected,
                decision_source = decision_source,
                classifier_provider = classifier_provider,
                classifier_model = classifier_model,
                classification_error = classification_error,
                classifier_response_shape = classifier_response_shape,
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
                classification_error = page.get("classification_error") or "all_other_pages_would_be_excluded",
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
