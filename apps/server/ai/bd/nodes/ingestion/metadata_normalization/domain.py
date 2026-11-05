"""Validation and recovery of evidence-backed metadata proposals."""
from __future__ import annotations
from .... import agents

import json
from collections.abc import Iterator
from typing import Any
from pydantic import ValidationError


def extract_proposal(response: Any) -> agents.schemas.BookMetadataProposal:
    """Validate structured output or recover a JSON proposal from the last assistant message."""
    structured = response.get("structured_response") if isinstance(response, dict) else None
    if structured is not None:
        return agents.schemas.BookMetadataProposal.model_validate(structured)

    messages = response.get("messages") if isinstance(response, dict) else None
    content = _last_assistant_content(messages)
    if content is None:
        raise ValueError("metadata normalizer returned no structured response or assistant message")

    last_error: ValidationError | json.JSONDecodeError | None = None
    for value in _json_objects(content):
        try:
            return agents.schemas.BookMetadataProposal.model_validate(value)
        except ValidationError as error:
            last_error = error

    if last_error is not None:
        raise ValueError("metadata normalizer returned JSON that did not match its response schema") from last_error
    raise ValueError("metadata normalizer returned no structured response and no JSON object")


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
