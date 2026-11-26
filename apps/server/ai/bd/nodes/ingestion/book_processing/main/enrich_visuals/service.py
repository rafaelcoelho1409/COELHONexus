"""Vision calls, resumable figure artifacts, and page-level Markdown replacement."""
from __future__ import annotations
import domains
from . import domain
from ...... import agents, graphs

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from langchain_core.messages import HumanMessage
from pydantic import ValidationError


logger = logging.getLogger(__name__)
PageReader = Callable[[str], str | None | Awaitable[str | None]]
FigureReader = Callable[[str], bytes | None | Awaitable[bytes | None]]
ArtifactWriter = Callable[[str, str, str], Any]
ArtifactExists = Callable[[str], Awaitable[bool]]
ImageMarker = re.compile(r"<!--\s*image\s*-->", re.IGNORECASE)


class VisualExtractor(Protocol):
    async def ainvoke(self, input: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]: ...


async def _read(reader: Callable, key: str) -> Any:
    value = reader(key)
    if inspect.isawaitable(value):
        value = await value
    return value


async def _write(writer: ArtifactWriter, key: str, content: str, content_type: str) -> None:
    value = writer(key, content, content_type)
    if inspect.isawaitable(value):
        await value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


async def enrich_visuals(
    state: graphs.ingestion.book_processing.states.IngestionRunState,
    read_page: PageReader,
    read_figure: FigureReader | None,
    write_artifact: ArtifactWriter,
    artifact_exists: ArtifactExists,
    extractor: VisualExtractor | None,
    progress_callback = None,
    cancel_check = None,
) -> dict[str, Any]:
    """Convert legible, confidently transcribed figures and keep their original crops available."""
    book_id = state.get("book_id")
    run_id = state.get("agentic_run_id")
    page_refs = state.get("source_page_refs")
    page_results = state.get("page_results")
    if not isinstance(book_id, str) or not isinstance(run_id, str):
        raise TypeError("visual enrichment requires book and run identifiers")
    if not isinstance(page_refs, list) or not isinstance(page_results, list):
        raise TypeError("visual enrichment requires source references and repaired page results")

    reference_by_page = {item.get("page"): item for item in page_refs if isinstance(item, dict)}
    result_by_page = {item.get("page"): item for item in page_results if isinstance(item, dict)}
    selection = {
        item.get("page_number"): item.get("disposition")
        for item in state.get("content_selection", [])
        if isinstance(item, dict)
    }
    metadata = state.get("book_metadata", {})
    title = metadata.get("title", "") if isinstance(metadata, dict) else ""
    eligible: dict[int, tuple[dict[str, Any], dict[str, Any], str, list[str], list[re.Match[str]]]] = {}
    for page_number in sorted(reference_by_page):
        reference = reference_by_page[page_number]
        result = result_by_page.get(page_number)
        if (
            not isinstance(page_number, int)
            or not isinstance(result, dict)
            or result.get("status") not in {"done", "review_required"}
            or result.get("delivery_disposition", "include") == "discarded_blank"
            or selection.get(page_number, "include") != "include"
        ):
            continue
        names = reference.get("figures", [])
        normalized_key = result.get("normalized_key")
        if not isinstance(names, list) or not names or not isinstance(normalized_key, str):
            continue
        markdown = await _read(read_page, normalized_key)
        if not isinstance(markdown, str):
            continue
        markers = list(ImageMarker.finditer(markdown))
        paired_names = names[:len(markers)]
        if not all(isinstance(name, str) and name.strip() for name in paired_names):
            continue
        if paired_names:
            eligible[page_number] = (reference, result, markdown, paired_names, markers)

    total = sum(len(item[3]) for item in eligible.values())
    await graphs.service.report(
        progress_callback,
        kind = "visual_enrichment_progress",
        candidate_count = total,
        completed_count = 0,
        converted_count = 0,
        failed_count = 0,
    )
    if total == 0:
        return {
            "page_results": page_results,
            "visual_candidate_count": 0,
            "visual_completed_count": 0,
            "visual_converted_count": 0,
            "visual_failed_count": 0,
        }

    semaphore = asyncio.Semaphore(domains.bd.ingestion.ai.params.VISUAL_ENRICHMENT_CONCURRENCY)
    progress_lock = asyncio.Lock()
    completed = 0
    converted = 0
    failed = 0
    update_by_page: dict[int, dict[str, Any]] = {}

    async def transcribe(
        page_number: int,
        reference: dict[str, Any],
        markdown: str,
        figure_name: str,
        marker: re.Match[str],
    ) -> dict[str, Any]:
        nonlocal completed, converted, failed
        if cancel_check is not None and await cancel_check():
            raise asyncio.CancelledError
        figure_key = domains.bd.ingestion.source.storage.keys.figure_key(
            book_id,
            figure_name,
            state["extraction_run_id"],
        )
        try:
            image = await _read(read_figure, figure_key) if read_figure is not None else None
        except Exception as error:  # noqa: BLE001 — missing source crops keep the original image marker.
            logger.warning(
                "[bd-visual-enrichment] source figure unavailable on page %s (%s)",
                page_number,
                type(error).__name__,
            )
            image = None
        context = domain.figure_context(
            markdown,
            marker.start(),
            limit = domains.bd.ingestion.ai.params.VISUAL_CONTEXT_CHARS,
        )
        if not isinstance(image, bytes) or not image or len(image) > domains.bd.ingestion.ai.params.VISUAL_FIGURE_MAX_BYTES:
            outcome = _retained(
                figure_name,
                "source figure is unavailable or exceeds the image-size limit",
                failed = True,
            )
        else:
            image_digest = _sha256(image)
            fingerprint = _context_fingerprint(
                state,
                reference,
                title,
                context,
                figure_name,
                image_digest,
            )
            reusable_fingerprints = {fingerprint}
            reusable_fingerprints.update(
                _context_fingerprint(
                    state,
                    reference,
                    title,
                    context,
                    figure_name,
                    image_digest,
                    visual_version = visual_version,
                )
                for visual_version in domains.bd.ingestion.ai.versions.VISUAL_ENRICHMENT_COMPATIBLE_VERSIONS
            )
            cache_key = domains.bd.ingestion.source.storage.keys.agentic_visual_figure_key(
                book_id,
                run_id,
                page_number,
                figure_name,
            )
            outcome = await _read_cached_outcome(
                read_page,
                artifact_exists,
                cache_key,
                reusable_fingerprints,
                image_digest,
                current_fingerprint = fingerprint,
            )
            if outcome is None:
                outcome = await _call_extractor(
                    extractor,
                    image,
                    page_number = page_number,
                    figure_name = figure_name,
                    title = str(title),
                    section_path = reference.get("section_path", []),
                    context = context,
                    run_id = run_id,
                )
                if outcome.get("cacheable"):
                    await _write(
                        write_artifact,
                        cache_key,
                        _json({
                            "fingerprint": fingerprint,
                            "image_sha256": image_digest,
                            "outcome": {key: value for key, value in outcome.items() if key != "cacheable"},
                        }),
                        "application/json",
                    )
        outcome["figure_name"] = figure_name
        if not outcome.get("markdown") and not outcome.get("failed"):
            logger.info(
                "[bd-visual-enrichment] retained page %s figure %s decision=%s kind=%s confidence=%s reason=%s",
                page_number,
                figure_name,
                outcome.get("decision", "unknown"),
                outcome.get("kind", "unknown"),
                outcome.get("confidence", "unknown"),
                outcome.get("reason_code", "unknown"),
            )
        async with progress_lock:
            if outcome.get("markdown"):
                converted += 1
            if outcome.get("failed"):
                failed += 1
            completed += 1
            await graphs.service.report(
                progress_callback,
                kind = "visual_figure",
                page = page_number,
                status = "failed" if outcome.get("failed") else "done",
                candidate_count = total,
                completed_count = completed,
                converted_count = converted,
                failed_count = failed,
            )
        return outcome

    async def process_page(page_number: int, data: tuple) -> None:
        reference, result, markdown, names, markers = data
        outputs = await asyncio.gather(*(
            _bounded_transcribe(
                semaphore,
                transcribe,
                page_number,
                reference,
                markdown,
                name,
                marker,
            )
            for name, marker in zip(names, markers, strict = False)
        ))
        converted_figures = [item["figure_name"] for item in outputs if item.get("markdown")]
        replacements = {item["figure_name"]: item.get("markdown") for item in outputs}
        chunks = []
        previous = 0
        for name, marker in zip(names, markers, strict = False):
            chunks.append(markdown[previous:marker.start()])
            replacement = replacements.get(name)
            chunks.append(replacement if isinstance(replacement, str) and replacement else marker.group(0))
            previous = marker.end()
        chunks.append(markdown[previous:])
        enriched = "".join(chunks)
        visual_key = domains.bd.ingestion.source.storage.keys.agentic_visual_artifacts_key(book_id, run_id, page_number)
        visual_payload = {
            "schema_version": domains.bd.ingestion.ai.versions.VISUAL_ENRICHMENT_VERSION,
            "page_number": page_number,
            "conversions": [
                {
                    "figure_name": item["figure_name"],
                    "converted": bool(item.get("markdown")),
                    "failed": bool(item.get("failed")),
                    "kind": item.get("kind"),
                    "decision": item.get("decision"),
                    "reason_code": item.get("reason_code"),
                    "confidence": item.get("confidence"),
                    "rationale": item.get("rationale", ""),
                }
                for item in outputs
            ],
        }
        visual_content = _json(visual_payload)
        await _write(write_artifact, visual_key, visual_content, "application/json")
        if converted_figures:
            enriched_key = domains.bd.ingestion.source.storage.keys.agentic_visual_page_key(book_id, run_id, page_number)
            await _write(write_artifact, enriched_key, enriched, "text/markdown; charset=utf-8")
            enriched_digest = _sha256(enriched)
        else:
            enriched_key = result["normalized_key"]
            enriched_digest = result["normalized_sha256"]
        update_by_page[page_number] = {
            **result,
            "enriched_key": enriched_key,
            "enriched_sha256": enriched_digest,
            "visual_artifacts_key": visual_key,
            "visual_artifacts_sha256": _sha256(visual_content),
            "converted_figures": converted_figures,
        }
        await graphs.service.report(
            progress_callback,
            kind = "visual_page",
            page = page_number,
            status = "done",
            converted_figures = converted_figures,
        )

    try:
        await asyncio.gather(*(process_page(number, data) for number, data in eligible.items()))
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Page-level source reads or persistence errors should fail the node so Resume can reuse saved figure calls.
        logger.exception("[bd-visual-enrichment] page materialization failed (%s)", type(error).__name__)
        raise

    new_results = []
    for result in page_results:
        page_number = result.get("page") if isinstance(result, dict) else None
        new_results.append(update_by_page.get(page_number, result))
    return {
        "page_results": new_results,
        "visual_candidate_count": total,
        "visual_completed_count": completed,
        "visual_converted_count": converted,
        "visual_failed_count": failed,
    }


async def _bounded_transcribe(semaphore, transcribe, *args):
    async with semaphore:
        return await transcribe(*args)


async def _read_cached_outcome(
    read_page,
    artifact_exists,
    key: str,
    fingerprints: set[str],
    image_digest: str,
    *,
    current_fingerprint: str,
):
    if not await artifact_exists(key):
        return None
    try:
        content = await _read(read_page, key)
        saved = json.loads(content) if isinstance(content, str) else None
        if (
            not isinstance(saved, dict)
            or saved.get("fingerprint") not in fingerprints
            or saved.get("image_sha256") != image_digest
        ):
            return None
        result = agents.schemas.VisualExtractionResult.model_validate(saved.get("outcome", {}).get("result"))
        rendered, reason = domain.render_conversion(result)
        # A visual-version change means the extraction prompt, schema handling, or serializers
        # improved. Reuse successful old conversions, but give every prior non-conversion one
        # fresh attempt; caption text alone is not reliable enough to identify diagrams.
        if not rendered and saved.get("fingerprint") != current_fingerprint:
            return None
        reason_code = _conversion_reason_code(result, rendered, reason)
        return {
            "markdown": rendered,
            "kind": result.kind.value if result.kind else None,
            "decision": result.decision,
            "reason_code": reason_code,
            "confidence": result.confidence,
            "rationale": reason if rendered else result.rationale,
            "cacheable": True,
            "failed": False,
        }
    except (ValueError, TypeError, ValidationError, json.JSONDecodeError):
        return None


async def _call_extractor(
    extractor: VisualExtractor | None,
    image: bytes,
    *,
    page_number: int,
    figure_name: str,
    title: str,
    section_path: Any,
    context: str,
    run_id: str,
) -> dict[str, Any]:
    if extractor is None:
        return _retained(
            figure_name,
            "vision agent is unavailable",
            cacheable = False,
            failed = True,
        )
    payload = {
        "task": "Transcribe this source figure faithfully into the requested structured response.",
        "book_title": title,
        "physical_page": page_number,
        "figure_name": figure_name,
        "section_path": section_path if isinstance(section_path, list) else [],
        "nearby_page_text": context,
    }
    image_url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    try:
        response = None
        result = None
        last_error = None
        for attempt in range(2):
            text = json.dumps(payload, ensure_ascii = False)
            if attempt:
                text += "\n\nThe previous response did not match the required schema. "
                text += _visual_validation_feedback(response, last_error)
            try:
                with domains.settings.llm_counter.service.llm_context(
                    ns = "bd", run_id = run_id, node_id = "visual_extractor",
                ):
                    response = await extractor.ainvoke(
                        {"messages": [HumanMessage(content = [
                            {"type": "text", "text": text},
                            {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                        ])]},
                        config = {"callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()]},
                    )
                result = _validated_visual_response(response)
                break
            except (ValidationError, ValueError) as error:
                last_error = error
                if attempt == 1:
                    raise
        if result is None:
            raise ValueError("the vision response did not match the visual extraction schema")
        rendered, reason = domain.render_conversion(result)
        reason_code = _conversion_reason_code(result, rendered, reason)
        return {
            "result": result.model_dump(mode = "json"),
            "markdown": rendered,
            "kind": result.kind.value if result.kind else None,
            "decision": result.decision,
            "reason_code": reason_code,
            "confidence": result.confidence,
            "rationale": reason if rendered else result.rationale,
            "cacheable": True,
            "failed": False,
        }
    except Exception as error:  # noqa: BLE001 — uncertain images remain available as source figures.
        details = _visual_error_summary(error)
        logger.warning(
            "[bd-visual-enrichment] retained page %s figure %s after %s: %s",
            page_number,
            figure_name,
            type(error).__name__,
            details,
        )
        return _retained(
            figure_name,
            f"vision transcription unavailable ({type(error).__name__}: {details})",
            cacheable = False,
            failed = True,
        )


def _validated_visual_response(response: Any) -> agents.schemas.VisualExtractionResult:
    candidates = _visual_response_candidates(response)
    content = _last_assistant_content(response)
    if content is not None:
        candidates.extend(_json_objects(content))

    last_error = None
    for candidate in candidates:
        try:
            return agents.schemas.VisualExtractionResult.model_validate(candidate)
        except ValidationError as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise ValueError("the vision agent returned no structured visual result")


def _visual_response_candidates(response: Any) -> list[Any]:
    """Search nested agent result envelopes, message bodies, and embedded JSON for the schema payload."""
    queue = [(response, 0)]
    candidates = []
    seen: set[int] = set()
    while queue and len(seen) < 256:
        value, depth = queue.pop(0)
        if value is None or id(value) in seen:
            continue
        seen.add(id(value))
        if depth > 12:
            continue
        mapping = _response_mapping(value)
        if mapping is not None:
            candidates.append(mapping)
            queue.extend((item, depth + 1) for item in mapping.values())
        elif isinstance(value, (list, tuple)):
            queue.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            parsed = _json_objects(value)
            candidates.extend(parsed)
            queue.extend((item, depth + 1) for item in parsed)
    return candidates


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


def _last_assistant_content(response: Any) -> str | None:
    response_mapping = _response_mapping(response)
    messages = response_mapping.get("messages") if response_mapping else None
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        message_mapping = _response_mapping(message)
        if message_mapping is not None:
            role = message_mapping.get("role") or message_mapping.get("type")
            content = message_mapping.get("content")
        else:
            role = getattr(message, "type", None)
            content = getattr(message, "content", None)
        if role not in {"assistant", "ai", "AIMessage"}:
            continue
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                mapped["text"] for item in content
                if (mapped := _response_mapping(item)) is not None and isinstance(mapped.get("text"), str)
            ]
            if text_parts:
                return "\n".join(text_parts)
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


def _visual_validation_feedback(response: Any, error: Exception | None) -> str:
    parts = [
        "Return only fields from the visual extraction schema. If the figure cannot be transcribed completely, use decision='retain'."
    ]
    if error is not None:
        parts.append("Schema errors: " + _visual_error_summary(error))
    response_mapping = _response_mapping(response)
    previous = response_mapping.get("structured_response") if response_mapping else None
    if previous is None:
        previous = _last_assistant_content(response)
    if previous is not None:
        try:
            rendered = json.dumps(previous, ensure_ascii = False, default = str)
        except (TypeError, ValueError):
            rendered = str(previous)
        parts.append("Previous response: " + rendered[:6000])
    return "\n".join(parts)


def _visual_error_summary(error: Exception) -> str:
    if isinstance(error, ValidationError):
        summary = [
            f"{'.'.join(str(part) for part in item.get('loc', ())) or 'response'}:{item.get('type', 'invalid')}"
            for item in error.errors()[:5]
        ]
        return ", ".join(summary) or "schema validation failed"
    return str(error).replace("\n", " ")[:240]


def _context_fingerprint(
    state,
    reference,
    title,
    context,
    figure_name,
    image_digest,
    *,
    visual_version: str | None = None,
) -> str:
    values = {
        "workflow_version": state.get("workflow_version"),
        "visual_version": visual_version or domains.bd.ingestion.ai.versions.VISUAL_ENRICHMENT_VERSION,
        "page": reference.get("page"),
        "section_path": reference.get("section_path", []),
        "title": title,
        "context": context,
        "figure_name": figure_name,
        "image_sha256": image_digest,
    }
    return _sha256(_json(values))


def _retained(name: str, reason: str, *, cacheable: bool = True, failed: bool = False) -> dict[str, Any]:
    return {
        "figure_name": name,
        "markdown": None,
        "kind": None,
        "decision": "retain",
        "reason_code": "vision_failure" if failed else "unavailable_source",
        "confidence": None,
        "rationale": reason,
        "cacheable": cacheable,
        "failed": failed,
    }


def _conversion_reason_code(
    result: agents.schemas.VisualExtractionResult,
    rendered: str | None,
    reason: str,
) -> str:
    if rendered:
        return "converted"
    if result.decision == "retain":
        return "model_retained"
    if "confidence" in reason:
        return "below_confidence"
    return "unsupported_or_invalid_output"
