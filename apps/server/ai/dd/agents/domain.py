"""Pure readers for what an agent answered (its structured response, or the JSON it wrote as plain text instead) and for how a model call failed."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any, TypeVar
import json

from httpx import TransportError
from openai import APIConnectionError
from pydantic import BaseModel, ValidationError


Schema = TypeVar("Schema", bound = BaseModel)


def is_transient(error: BaseException) -> bool:
    """Whether a model call failed for a reason a new call can fix: a timeout, a dropped connection, a rate limit or a provider 5xx."""
    if isinstance(error, (TimeoutError, APIConnectionError, TransportError)):
        return True
    status_code = getattr(error, "status_code", None)
    return status_code in (408, 429) or (isinstance(status_code, int) and 500 <= status_code <= 599)


def rotator_meta(result: Any) -> dict[str, Any]:
    """What the Rotator told us about the call that produced `result`: its request id (a response header on the last assistant
    message), which `report_quality` needs to tell the Rotator how good the answer was. {} when the endpoint is not a Rotator."""
    messages = result.get("messages") if isinstance(result, dict) else None
    raw = result.get("raw") if isinstance(result, dict) else None
    if raw is not None:
        messages = [*(messages or []), raw]
    for message in reversed(messages or []):
        if getattr(message, "type", None) not in {"ai", "assistant"}:
            continue
        headers = (getattr(message, "response_metadata", None) or {}).get("headers") or {}
        request_id = headers.get("x-rotator-request-id") if isinstance(headers, dict) else None
        return {"rotator_request_id": request_id} if request_id else {}
    return {}


def structured_or_recovered(result: Any, schema: type[Schema]) -> Schema | None:
    """The agent's `structured_response`, else the first JSON object in its last assistant message that fits `schema`.

    Some models close their turn with the answer as a fenced JSON block instead of calling the structured-output tool,
    which leaves `structured_response` empty even though the answer is complete and valid."""
    if not isinstance(result, dict):
        return None
    structured = result.get("structured_response")
    parsed = result.get("parsed")
    for candidate in (structured, parsed):
        if candidate is None:
            continue
        try:
            return schema.model_validate(candidate)
        except ValidationError:
            pass

    raw = result.get("raw")
    for arguments in _tool_call_arguments(raw):
        try:
            return schema.model_validate(arguments)
        except ValidationError:
            continue

    content = _last_assistant_content([*(result.get("messages") or []), *([raw] if raw is not None else [])])
    if content is None:
        return None
    for value in _json_objects(content):
        try:
            return schema.model_validate(value)
        except ValidationError:
            continue
    return None


def structured_failure_reason(result: Any, schema: type[Schema]) -> str:
    """Describe why a response failed schema parsing without persisting its text.

    Planner health is user-visible, so include concise validation locations but
    never copy raw model output (which may repeat source content) into plan
    metadata.
    """
    if not isinstance(result, dict):
        return "agent returned no state object"

    structured = result.get("structured_response")
    parsed = result.get("parsed")
    for label, candidate in (("structured response", structured), ("parsed response", parsed)):
        if candidate is None:
            continue
        try:
            schema.model_validate(candidate)
        except ValidationError as error:
            fields = [
                f"{'.'.join(str(part) for part in item.get('loc') or ()) or '$'} ({item.get('type', 'invalid')})"
                for item in error.errors(include_input = False)[:6]
            ]
            return f"{label} failed validation: " + ", ".join(fields)
        return f"agent returned a {label} that could not be recovered"

    raw = result.get("raw")
    tool_errors = []
    for arguments in _tool_call_arguments(raw):
        try:
            schema.model_validate(arguments)
        except ValidationError as error:
            fields = [
                f"{'.'.join(str(part) for part in item.get('loc') or ()) or '$'} ({item.get('type', 'invalid')})"
                for item in error.errors(include_input = False)[:6]
            ]
            tool_errors.append(", ".join(fields))
    if tool_errors:
        return "function-call arguments failed validation: " + tool_errors[0]

    messages = [*(result.get("messages") or []), *([raw] if raw is not None else [])]
    content = _last_assistant_content(messages)
    if content is None:
        if raw is not None:
            metadata = getattr(raw, "response_metadata", None) or {}
            finish = metadata.get("finish_reason") if isinstance(metadata, dict) else None
            names = [
                str(call.get("name"))
                for call in (getattr(raw, "tool_calls", None) or [])
                if isinstance(call, dict) and call.get("name")
            ]
            parsing_error = result.get("parsing_error")
            details = []
            if finish:
                details.append(f"finish_reason={finish}")
            if names:
                details.append(f"tool_calls={','.join(names[:4])}")
            if parsing_error is not None:
                details.append(f"parser_error={type(parsing_error).__name__}")
            suffix = f" ({'; '.join(details)})" if details else ""
            return "direct model returned neither a usable function call nor assistant text" + suffix
        return "agent returned neither structured data nor assistant text"

    json_seen = False
    for value in _json_objects(content):
        json_seen = True
        try:
            schema.model_validate(value)
        except ValidationError as error:
            fields = [
                f"{'.'.join(str(part) for part in item.get('loc') or ()) or '$'} ({item.get('type', 'invalid')})"
                for item in error.errors(include_input = False)[:6]
            ]
            return "assistant JSON failed validation: " + ", ".join(fields)
    if json_seen:
        return "assistant JSON did not produce a valid structured answer"
    return f"assistant returned non-JSON text ({len(content)} characters)"


def _last_assistant_content(messages: Any) -> str | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role") or message.get("type")
            content = message.get("content")
        else:
            role = getattr(message, "type", None)
            content = getattr(message, "content", None)
        if role not in {"assistant", "ai", "AIMessage"}:
            continue
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block["text"]
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if parts:
                return "\n".join(parts)
    return None


def _tool_call_arguments(message: Any) -> Iterator[dict[str, Any]]:
    """Yield structured arguments from LangChain tool calls without exposing their contents in diagnostics."""
    for call in getattr(message, "tool_calls", None) or []:
        if isinstance(call, dict) and isinstance(call.get("args"), dict):
            yield call["args"]

    additional = getattr(message, "additional_kwargs", None) or {}
    calls = additional.get("tool_calls") if isinstance(additional, dict) else None
    for call in calls or []:
        function = call.get("function") if isinstance(call, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if isinstance(arguments, dict):
            yield arguments
        elif isinstance(arguments, str):
            try:
                value = json.loads(arguments)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _json_objects(content: str) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    for start, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value
