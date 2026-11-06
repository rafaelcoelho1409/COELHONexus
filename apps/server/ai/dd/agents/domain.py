"""Pure readers for what an agent answered (its structured response, or the JSON it wrote as plain text instead) and for how a model call failed."""
from __future__ import annotations

from collections.abc import Iterator
from json import JSONDecodeError, JSONDecoder
from typing import Any, TypeVar

import httpx
from openai import APIConnectionError
from pydantic import BaseModel, ValidationError


Schema = TypeVar("Schema", bound = BaseModel)


def is_transient(error: BaseException) -> bool:
    """Whether a model call failed for a reason a new call can fix: a timeout, a dropped connection, a rate limit or a provider 5xx."""
    if isinstance(error, (TimeoutError, APIConnectionError, httpx.TransportError)):
        return True
    status_code = getattr(error, "status_code", None)
    return status_code in (408, 429) or (isinstance(status_code, int) and 500 <= status_code <= 599)


def rotator_meta(result: Any) -> dict[str, Any]:
    """What the Rotator told us about the call that produced `result`: its request id (a response header on the last assistant
    message), which `report_quality` needs to tell the Rotator how good the answer was. {} when the endpoint is not a Rotator."""
    messages = result.get("messages") if isinstance(result, dict) else None
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
    if structured is not None:
        try:
            return schema.model_validate(structured)
        except ValidationError:
            pass
    content = _last_assistant_content(result.get("messages"))
    if content is None:
        return None
    for value in _json_objects(content):
        try:
            return schema.model_validate(value)
        except ValidationError:
            continue
    return None


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


def _json_objects(content: str) -> Iterator[dict[str, Any]]:
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
