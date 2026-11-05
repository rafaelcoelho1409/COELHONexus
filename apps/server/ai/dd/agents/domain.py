"""Pure readers for what an agent answered: its structured response, or the JSON it wrote as plain text instead."""
from __future__ import annotations

from collections.abc import Iterator
from json import JSONDecodeError, JSONDecoder
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError


Schema = TypeVar("Schema", bound = BaseModel)


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
