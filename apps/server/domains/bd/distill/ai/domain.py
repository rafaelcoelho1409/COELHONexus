"""Pure transformations and validation rules for Book Distiller's Distill feature."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from json import JSONDecodeError, JSONDecoder, loads
from typing import Any

from json_repair import loads as json_repair_loads
from pydantic import BaseModel, ValidationError

from . import entities, params, schemas

_WHITESPACE_RE = re.compile(r"\s+")
_SOFT_HYPHEN_RE = re.compile(r"[\u00ad\u200b\u200c\u200d\ufeff]")
_LINE_HYPHEN_RE = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")
_QUOTE_TRANSLATION = str.maketrans({
    "‘": "'",
    "’": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
})
_DISTILL_CHAPTER_HEADING = re.compile(r"^(?:chapter|appendix)\s+", re.IGNORECASE)
_BOOK_SYNTHESIS_OUTPUT_ALIASES = {"connections": "cross_chapter_connections"}
_BOOK_SYNTHESIS_OUTPUT_ITEMS = {
    "key_takeaways": "Key takeaway",
    "cross_chapter_connections": "Cross-chapter connection",
    "limitations": "Limitation",
}


def learning_extraction_from_agent_result(result: Any) -> schemas.LearningExtraction | None:
    """Read a typed extraction, recovering valid JSON when the agent omitted its structured field."""
    if not isinstance(result, dict):
        return None

    for candidate in (result.get("structured_response"), result.get("parsed")):
        extraction = _validate_learning_extraction(candidate)
        if extraction is not None:
            return extraction

    raw = result.get("raw")
    for message in [*(result.get("messages") or []), *([raw] if raw is not None else [])]:
        for candidate in _tool_call_arguments(message):
            extraction = _validate_learning_extraction(candidate)
            if extraction is not None:
                return extraction
        content = _assistant_content(message)
        for candidate in _json_objects(content):
            extraction = _validate_learning_extraction(candidate)
            if extraction is not None:
                return extraction
    return None


def structured_model_from_agent_result(result: Any, response_model: type[BaseModel]) -> BaseModel | None:
    """Validate a typed DeepAgent response, recovering a JSON object from assistant text if needed."""
    if isinstance(result, response_model):
        return result
    if isinstance(result, BaseModel):
        result = {"structured_response": result}
    if not isinstance(result, Mapping):
        return None
    candidates = [result.get("structured_response"), result.get("parsed")]
    if any(field in result for field in response_model.model_fields):
        candidates.append(result)
    raw = result.get("raw")
    for message in [*(result.get("messages") or []), *([raw] if raw is not None else [])]:
        candidates.extend(_tool_call_arguments(message))
        candidates.extend(_json_objects(_assistant_content(message)))
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, response_model):
            return candidate
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump(mode = "python")
        try:
            return response_model.model_validate(candidate)
        except ValidationError:
            continue
    return None


def report_review_from_agent_result(result: Any) -> schemas.ReportReviewOutput | None:
    """Recover a report review while validating every finding against the public schema.

    Some OpenAI-compatible providers include extra wrapper or metadata fields around
    structured output. Those fields are irrelevant to editorial findings, so retain
    only the schema fields while still rejecting malformed findings.
    """
    if isinstance(result, schemas.ReportReviewOutput):
        return result
    if isinstance(result, BaseModel):
        result = {"structured_response": result}
    if not isinstance(result, Mapping):
        return None

    candidates: list[Any] = [result.get("structured_response"), result.get("parsed")]
    if "findings" in result:
        candidates.append(result)
    raw = result.get("raw")
    for message in [*(result.get("messages") or []), *([raw] if raw is not None else [])]:
        candidates.extend(_tool_call_arguments(message))
        candidates.extend(_json_objects(_assistant_content(message)))

    review_fields = ("severity", "area", "issue", "recommendation")
    for candidate in candidates:
        if isinstance(candidate, schemas.ReportReviewOutput):
            return candidate
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump(mode = "python")
        for _ in range(3):
            if not isinstance(candidate, Mapping) or "findings" in candidate:
                break
            nested = next(
                (
                    candidate.get(key)
                    for key in ("report_review", "review", "output", "result")
                    if isinstance(candidate.get(key), (Mapping, BaseModel))
                ),
                None,
            )
            if nested is None:
                break
            candidate = nested.model_dump(mode = "python") if isinstance(nested, BaseModel) else nested
        if not isinstance(candidate, Mapping):
            continue

        findings = candidate.get("findings")
        if isinstance(findings, str):
            try:
                findings = loads(findings)
            except JSONDecodeError:
                continue
        if isinstance(findings, Mapping):
            findings = [findings]
        if not isinstance(findings, list):
            continue

        parsed_findings: list[schemas.ReportReviewFinding] = []
        for finding in findings:
            if isinstance(finding, str):
                try:
                    finding = loads(finding)
                except JSONDecodeError:
                    break
            if isinstance(finding, BaseModel):
                finding = finding.model_dump(mode = "python")
            if not isinstance(finding, Mapping):
                break
            try:
                parsed_findings.append(
                    schemas.ReportReviewFinding.model_validate({
                        key: finding.get(key) for key in review_fields
                    }),
                )
            except ValidationError:
                break
        else:
            return schemas.ReportReviewOutput(findings = parsed_findings)
    return None


def chapter_synthesis_from_agent_result(result: Any) -> schemas.ChapterSynthesis | None:
    """Parse a chapter guide from provider output, including a single known wrapper object."""
    if isinstance(result, schemas.ChapterSynthesis):
        return result
    if isinstance(result, BaseModel):
        result = {"structured_response": result}
    if not isinstance(result, dict):
        return None

    for candidate in (result.get("structured_response"), result.get("parsed")):
        synthesis = _validate_chapter_synthesis(candidate)
        if synthesis is not None:
            return synthesis

    raw = result.get("raw")
    for message in [*(result.get("messages") or []), *([raw] if raw is not None else [])]:
        for candidate in _tool_call_arguments(message):
            synthesis = _validate_chapter_synthesis(candidate)
            if synthesis is not None:
                return synthesis
        content = _assistant_content(message)
        for candidate in _json_objects(content):
            synthesis = _validate_chapter_synthesis(candidate)
            if synthesis is not None:
                return synthesis
    return None


def chapter_synthesis_validation_issues(result: Any) -> list[str]:
    """Return safe schema paths and error types for an invalid chapter response."""
    if isinstance(result, schemas.ChapterSynthesis):
        return []
    if isinstance(result, BaseModel):
        candidates: list[Any] = [result]
    elif isinstance(result, dict):
        candidates = [result.get("structured_response"), result.get("parsed")]
        raw = result.get("raw")
        for message in [*(result.get("messages") or []), *([raw] if raw is not None else [])]:
            candidates.extend(_tool_call_arguments(message))
            candidates.extend(_json_objects(_assistant_content(message)))
    else:
        return ["no_valid_agent_response"]

    issues: list[str] = []
    for value in candidates:
        for candidate in _chapter_synthesis_candidates(value):
            try:
                schemas.ChapterSynthesis.model_validate(candidate)
            except ValidationError as error:
                issues.extend(
                    f"{'.'.join(str(part) for part in item['loc']) or '$'}:{item['type']}"
                    for item in error.errors(include_input = False)
                )
            else:
                return []
    return list(dict.fromkeys(issues))[:20] or ["no_valid_json_object"]


def book_synthesis_from_agent_result(result: Any) -> schemas.BookSynthesis | None:
    """Parse a whole-book guide from provider output, including a known wrapper object."""
    if isinstance(result, schemas.BookSynthesis):
        return result
    if isinstance(result, BaseModel):
        result = {"structured_response": result}
    for candidate in _book_synthesis_output_candidates(result):
        synthesis = _validate_book_synthesis(candidate)
        if synthesis is not None:
            return synthesis
    return None


def book_synthesis_validation_issues(result: Any) -> list[str]:
    """Return safe schema paths/types for invalid model output without logging its content."""
    issues = []
    for value in _book_synthesis_output_candidates(result):
        for candidate in _book_synthesis_candidates(value):
            try:
                schemas.BookSynthesisOutput.model_validate(
                    _normalize_book_synthesis_output(candidate),
                )
            except ValidationError as error:
                issues.extend(
                    f"{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
                    for item in error.errors(include_input = False)
                )
            else:
                return []
    return list(dict.fromkeys(issues)) or ["no_valid_json_object"]


def book_synthesis_output_from_agent_result(result: Any) -> schemas.BookSynthesisOutput | None:
    """Parse a bounded whole-book synthesis response from provider output."""
    for candidate in _book_synthesis_output_candidates(result):
        for unwrapped in _book_synthesis_candidates(candidate):
            try:
                return schemas.BookSynthesisOutput.model_validate(
                    _normalize_book_synthesis_output(unwrapped),
                )
            except ValidationError:
                continue
    return None


def _normalize_book_synthesis_output(value: Any) -> Any:
    """Normalize known provider shape drift while retaining strict schema validation."""
    if not isinstance(value, dict):
        return value

    normalized = dict(value)
    for alias, canonical in _BOOK_SYNTHESIS_OUTPUT_ALIASES.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized[alias]
        normalized.pop(alias, None)

    for field, label in _BOOK_SYNTHESIS_OUTPUT_ITEMS.items():
        items = normalized.get(field)
        if not isinstance(items, list):
            continue
        normalized_items = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            normalized_item = dict(item)
            title = normalized_item.get("title")
            if not isinstance(title, str) or not title.strip():
                explanation = normalized_item.get("explanation")
                normalized_item["title"] = (
                    _book_synthesis_title(explanation)
                    if isinstance(explanation, str) and explanation.strip()
                    else f"{label} {index + 1}"
                )
            normalized_items.append(normalized_item)
        normalized[field] = normalized_items
    return normalized


def _book_synthesis_title(explanation: str) -> str:
    """Derive a readable short title from an explanation omitted by the model."""
    first_sentence = re.split(r"(?<=[.!?])\s+", explanation.strip(), maxsplit = 1)[0]
    title = _WHITESPACE_RE.sub(" ", first_sentence).strip(" -*\t\r\n")
    if len(title) > 100:
        title = title[:97].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return title or "Book insight"


def _validate_chapter_synthesis(value: Any) -> schemas.ChapterSynthesis | None:
    for candidate in _chapter_synthesis_candidates(value):
        try:
            return schemas.ChapterSynthesis.model_validate(candidate)
        except ValidationError:
            continue
    return None


def _validate_book_synthesis(value: Any) -> schemas.BookSynthesis | None:
    for candidate in _book_synthesis_candidates(value):
        try:
            return schemas.BookSynthesis.model_validate(candidate)
        except ValidationError:
            continue
    return None


def _book_synthesis_output_candidates(result: Any) -> Iterator[Any]:
    if isinstance(result, BaseModel):
        yield result.model_dump(mode = "python")
        return
    if not isinstance(result, Mapping):
        return

    yield result.get("structured_response")
    yield result.get("parsed")
    output_fields = set(schemas.BookSynthesisOutput.model_fields)
    accepted_root_fields = output_fields | set(_BOOK_SYNTHESIS_OUTPUT_ALIASES)
    required_root_fields = output_fields - {"cross_chapter_connections"}
    has_connections = (
        "cross_chapter_connections" in result
        or "connections" in result
    )
    if (
        required_root_fields.issubset(result)
        and has_connections
        and set(result).issubset(accepted_root_fields)
    ):
        yield dict(result)
    raw = result.get("raw")
    for message in [*(result.get("messages") or []), *([raw] if raw is not None else [])]:
        yield from _tool_call_arguments(message)
        yield from _json_objects(_assistant_content(message))


def _chapter_synthesis_candidates(value: Any) -> Iterator[dict[str, Any]]:
    """Yield the root response and unwrap only a single recognized provider envelope."""
    candidate = value
    for _ in range(4):
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump(mode = "python")
        if not isinstance(candidate, dict):
            return
        yield candidate
        if len(candidate) != 1:
            return
        key, nested = next(iter(candidate.items()))
        if key not in {"chapter", "data", "output", "response", "result", "section", "synthesis"}:
            return
        if isinstance(nested, BaseModel):
            nested = nested.model_dump(mode = "python")
        if not isinstance(nested, dict):
            return
        candidate = nested


def _book_synthesis_candidates(value: Any) -> Iterator[dict[str, Any]]:
    """Yield the root response and unwrap one recognized whole-book response envelope."""
    candidate = value
    for _ in range(4):
        if isinstance(candidate, BaseModel):
            candidate = candidate.model_dump(mode = "python")
        if not isinstance(candidate, dict):
            return
        yield candidate
        if len(candidate) != 1:
            return
        key, nested = next(iter(candidate.items()))
        if key not in {"book", "output", "result", "synthesis"}:
            return
        if isinstance(nested, BaseModel):
            nested = nested.model_dump(mode = "python")
        if not isinstance(nested, dict):
            return
        candidate = nested


def _validate_learning_extraction(value: Any) -> schemas.LearningExtraction | None:
    if value is None:
        return None
    try:
        return schemas.LearningExtraction.model_validate(value)
    except ValidationError:
        return None


def _assistant_content(message: Any) -> str | None:
    if isinstance(message, dict):
        role = message.get("role") or message.get("type")
        content = message.get("content")
    else:
        role = getattr(message, "type", None)
        content = getattr(message, "content", None)
    if role not in {"assistant", "ai", "AIMessage"}:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "\n".join(parts) if parts else None
    return None


def _tool_call_arguments(message: Any) -> Iterator[dict[str, Any]]:
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        arguments = call.get("args")
        if isinstance(arguments, dict):
            yield arguments
            continue
        function = call.get("function")
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if isinstance(arguments, dict):
            yield arguments
        elif isinstance(arguments, str):
            try:
                value = loads(arguments)
            except JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value

    additional = (
        message.get("additional_kwargs") if isinstance(message, dict)
        else getattr(message, "additional_kwargs", None)
    ) or {}
    calls = additional.get("tool_calls") if isinstance(additional, dict) else None
    for call in calls or []:
        function = call.get("function") if isinstance(call, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if isinstance(arguments, dict):
            yield arguments
        elif isinstance(arguments, str):
            try:
                value = loads(arguments)
            except JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _json_objects(content: str | None) -> Iterator[dict[str, Any]]:
    if not content:
        return
    try:
        repaired = json_repair_loads(content)
    except Exception:  # noqa: BLE001 — strict parsing below can still recover a later JSON object.
        repaired = None
    if isinstance(repaired, dict):
        yield repaired
    decoder = JSONDecoder()
    for start, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_sha256(value: dict[str, Any]) -> str:
    """Hash a JSON-compatible mapping using the stable Distill serialization format."""
    body = json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)
    return sha256_text(body)


def json_body(value: Any) -> str:
    """Serialize a Distill artifact using stable, compact JSON."""
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


def graph_identity(state: Mapping[str, Any]) -> dict[str, Any]:
    """Project run identity fields into LangGraph fan-out payloads."""
    return {
        key: state[key]
        for key in (
            "book_id",
            "run_id",
            "extraction_run_id",
            "ingestion_ai_run_id",
            "source_manifest_key",
            "source_manifest_sha256",
            "ingestion_ai_manifest_key",
            "ingestion_ai_manifest_sha256",
            "content_selection_key",
            "content_selection_sha256",
            "content_selection_revision",
            "expected_page_count",
            "selected_page_count",
            "run_created_at",
        )
        if key in state
    }


def failure_context(
    error: Exception,
    progress: Mapping[str, Any],
    *,
    provider_kind: str,
    failed_at: float,
) -> dict[str, Any]:
    """Capture safe node/provider diagnostics without persisting exception bodies."""
    node_status = progress.get("node_status")
    node_status = node_status if isinstance(node_status, Mapping) else {}
    node = progress.get("current_node")
    failed_nodes = [name for name, status in node_status.items() if status == "failed"]
    if failed_nodes:
        node = node if node in failed_nodes else failed_nodes[-1]
    task = progress.get("current_task") if progress.get("current_node") == node else None
    active_tasks = progress.get("active_tasks")
    if not isinstance(task, str) and isinstance(active_tasks, Mapping) and isinstance(node, str):
        task = active_tasks.get(node)
    status_code = getattr(error, "status_code", None)
    if type(status_code) is not int or not 100 <= status_code <= 599:
        status_code = None
    return {
        "node": node[:100] if isinstance(node, str) else None,
        "task": task[:240] if isinstance(task, str) else None,
        "error_type": type(error).__name__[:120],
        "provider_kind": provider_kind,
        "status_code": status_code,
        "failed_at": failed_at,
    }


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]


def split_source_page(
    markdown: str,
    *,
    page_number: int,
    source_key: str,
    source_sha256: str,
    section_path: list[str],
    max_chars: int = params.MAX_SOURCE_UNIT_CHARS,
) -> list[tuple[entities.UnitRef, str]]:
    """Split one page at whitespace boundaries, retaining physical-page offsets and provenance."""
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    pieces: list[tuple[entities.UnitRef, str]] = []
    start = 0
    part = 0
    while start < len(markdown):
        while start < len(markdown) and markdown[start].isspace():
            start += 1
        if start >= len(markdown):
            break
        end = min(start + max_chars, len(markdown))
        if end < len(markdown):
            boundary = max(markdown.rfind("\n\n", start, end), markdown.rfind(" ", start, end), markdown.rfind("\n", start, end))
            if boundary > start + max_chars // 2:
                end = boundary
        text = markdown[start:end].strip()
        if not text:
            start = max(end, start + 1)
            continue
        actual_start = markdown.find(text, start, end)
        actual_end = actual_start + len(text)
        unit_id = stable_id(str(page_number), str(part), source_sha256, sha256_text(text))
        ref: entities.UnitRef = {
            "unit_id": unit_id,
            "input_key": "",
            "page_number": page_number,
            "section_path": list(section_path),
            "source_sha256": source_sha256,
            "text_sha256": sha256_text(text),
            "char_start": actual_start,
            "char_end": actual_end,
        }
        pieces.append((ref, text))
        start = actual_end
        part += 1
    return pieces


def quote_is_supported(quote: str, source_text: str) -> bool:
    """Match citations across PDF line wraps and equivalent Unicode punctuation."""
    normalized_quote = _normalize_source_quote(quote)
    normalized_source = _normalize_source_quote(source_text)
    return bool(normalized_quote) and normalized_quote in normalized_source


def _normalize_source_quote(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_QUOTE_TRANSLATION)
    normalized = _SOFT_HYPHEN_RE.sub("", normalized)
    normalized = _LINE_HYPHEN_RE.sub("", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def external_quote_is_supported(quote: str, source_text: str) -> bool:
    """Require an external evidence quote to occur verbatim, allowing only whitespace changes."""
    normalized_quote = _WHITESPACE_RE.sub(" ", quote).strip()
    normalized_source = _WHITESPACE_RE.sub(" ", source_text).strip()
    return bool(normalized_quote) and normalized_quote in normalized_source


def group_by_section(
    refs: Iterable[entities.UnitRef],
) -> list[tuple[tuple[str, ...], list[entities.UnitRef]]]:
    groups: dict[tuple[str, ...], list[entities.UnitRef]] = {}
    for ref in refs:
        path = tuple(part.strip() for part in ref["section_path"] if part.strip()) or ("Unassigned material",)
        groups.setdefault(path, []).append(ref)
    return sorted(groups.items(), key = lambda item: min(ref["page_number"] for ref in item[1]))


def processed_contents_structure(
    contents: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> tuple[dict[int, list[str]], list[dict[str, Any]], list[str]]:
    """Derive Distill section paths and chapter ranges from the displayed processed-book contents."""
    page_rows = sorted(
        (
            page for page in pages
            if type(page.get("source_page_number")) is int
            and type(page.get("processed_page_number")) is int
        ),
        key = lambda page: page["processed_page_number"],
    )
    if not page_rows:
        return {}, [], []

    page_by_source_number = {page["source_page_number"]: page for page in page_rows}

    entries = [
        (index, entry) for index, entry in enumerate(contents)
        if isinstance(entry.get("title"), str)
        and entry["title"].strip()
        and type(entry.get("processed_page_number")) is int
        and type(entry.get("source_page_number")) is int
    ]
    entries.sort(key = lambda row: (row[1]["processed_page_number"], row[0]))

    stack: list[tuple[int, list[str]]] = []
    events: list[tuple[int, list[str], dict[str, Any]]] = []
    chapter_candidates: list[tuple[int, dict[str, Any], list[str]]] = []
    mapping_notes: list[str] = []
    for _, entry in entries:
        requested_level = entry.get("level", 1)
        level = max(1, min(requested_level if type(requested_level) is int else 1, 8))
        while stack and stack[-1][0] >= level:
            stack.pop()
        path = [*stack[-1][1], entry["title"].strip()] if stack else [entry["title"].strip()]
        stack.append((level, path))
        processed_page_number = entry["processed_page_number"]
        is_chapter_heading = level == 1 or _DISTILL_CHAPTER_HEADING.match(entry["title"].strip())
        if is_chapter_heading:
            page = page_by_source_number.get(entry["source_page_number"], {})
            role = page.get("role")
            is_appendix = re.match(r"^appendix(?:\s|$)", entry["title"].strip(), re.IGNORECASE) is not None
            ignored_role = role in {"preface", "navigation", "legal", "noise"} or (
                role == "reference" and not is_appendix
            )
            if ignored_role:
                mapping_notes.append(
                    f"Contents heading '{entry['title'].strip()}' points to page "
                    f"{entry['source_page_number']} marked as {role}; it was ignored as a chapter boundary."
                )
                continue
            if entry.get("review_required"):
                mapping_notes.append(
                    f"Contents mapping for '{entry['title'].strip()}' on page "
                    f"{entry['source_page_number']} was marked for review by Ingestion."
                )
            chapter_candidates.append((entry["source_page_number"], entry, path))
        events.append((processed_page_number, path, entry))

    reviewed_titles = {
        " ".join(entry["title"].casefold().split())
        for _, entry, _ in chapter_candidates
        if not entry.get("review_required")
    }
    uncertain_duplicate_entries = {
        id(entry)
        for _, entry, _ in chapter_candidates
        if entry.get("review_required")
        and " ".join(entry["title"].casefold().split()) in reviewed_titles
    }
    if uncertain_duplicate_entries:
        for _, entry, _ in chapter_candidates:
            if id(entry) in uncertain_duplicate_entries:
                mapping_notes.append(
                    f"The review-required contents mapping for '{entry['title'].strip()}' on page "
                    f"{entry['source_page_number']} was ignored in favor of a reviewed chapter boundary."
                )
        chapter_candidates = [
            item for item in chapter_candidates if id(item[1]) not in uncertain_duplicate_entries
        ]
        events = [event for event in events if id(event[2]) not in uncertain_duplicate_entries]

    section_paths: dict[int, list[str]] = {}
    event_index = 0
    active_path: list[str] = []
    for page in page_rows:
        while event_index < len(events) and events[event_index][0] <= page["processed_page_number"]:
            active_path = events[event_index][1]
            event_index += 1
        section_paths[page["source_page_number"]] = list(active_path)

    titled_chapters = [
        row for row in chapter_candidates
        if _DISTILL_CHAPTER_HEADING.match(row[1]["title"].strip())
    ]
    selected_chapters = titled_chapters or [row for row in chapter_candidates if row[1].get("level") == 1]
    chapter_starts_by_page = {page: (entry, path) for page, entry, path in selected_chapters}
    chapter_starts = sorted(chapter_starts_by_page.items())
    last_source_page = max(page["source_page_number"] for page in page_rows)
    chapters = []
    for index, (first_page, (entry, path)) in enumerate(chapter_starts):
        next_first_page = chapter_starts[index + 1][0] if index + 1 < len(chapter_starts) else None
        last_page = next_first_page - 1 if next_first_page is not None else last_source_page
        if last_page < first_page:
            continue
        chapters.append({
            "title": entry["title"].strip(),
            "section_path": path,
            "first_page": first_page,
            "last_page": min(last_page, last_source_page),
        })
    return section_paths, chapters, sorted(set(mapping_notes))


def validate_synthesis_unit_ids(unit_ids: Iterable[str], available_ids: set[str]) -> list[str]:
    selected = list(dict.fromkeys(unit_ids))
    if not selected:
        raise ValueError("synthesis must cite at least one extracted learning unit")
    missing = set(selected) - available_ids
    if missing:
        raise ValueError("synthesis referenced unknown learning unit IDs")
    return selected
