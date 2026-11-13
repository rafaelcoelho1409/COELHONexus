"""Structured-response recovery and invocation for YCS DeepAgents."""
from __future__ import annotations
import domains

from collections.abc import Callable
from json import JSONDecodeError, JSONDecoder
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError


Schema = TypeVar("Schema", bound = BaseModel)


def structured_or_recovered(result: Any, schema: type[Schema]) -> Schema | None:
    """Read the typed response, recovering JSON text when a model skips structured output."""
    if not isinstance(result, dict):
        return None
    structured = result.get("structured_response")
    if structured is not None:
        try:
            return schema.model_validate(structured)
        except ValidationError:
            pass
    messages = result.get("messages")
    if not isinstance(messages, list):
        return None
    content = None
    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role") or message.get("type")
        else:
            role = getattr(message, "type", None)
        if role not in {"assistant", "ai", "AIMessage"}:
            continue
        value = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(value, str):
            content = value
            break
        if isinstance(value, list):
            text_parts = [
                part["text"]
                for part in value
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if text_parts:
                content = "\n".join(text_parts)
                break
    if not content:
        return None
    decoder = JSONDecoder()
    for start, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        try:
            return schema.model_validate(value)
        except ValidationError:
            continue
    return None


async def ask(
    agent: Any,
    schema: type[Schema],
    payload: str,
    *,
    attempts: int = 2,
    wait_base_s: float = 1.0,
    config: dict[str, Any] | None = None,
    check: Callable[[Schema], bool] | None = None,
) -> Schema:
    """Invoke a YCS DeepAgent, retrying transient failures and unusable structured answers."""
    request = {"messages": [{"role": "user", "content": payload}], "files": {}}

    def usable(result: Any) -> bool:
        parsed = structured_or_recovered(result, schema)
        return parsed is not None and (check is None or check(parsed))

    result = await domains.settings.chat.service.resilient_ainvoke(
        lambda: agent.ainvoke(request, config = config),
        attempts = attempts,
        wait_base_s = wait_base_s,
        accept = usable,
    )
    messages = result.get("messages") if isinstance(result, dict) else None
    if isinstance(messages, list):
        for message in messages:
            if getattr(message, "usage_metadata", None):
                await domains.ycs.ask.rag.service.capture_llm_usage(message)
    answer = structured_or_recovered(result, schema)
    if answer is None or (check is not None and not check(answer)):
        raise ValueError(f"the YCS agent ended without a valid {schema.__name__}")
    return answer
