"""Book Distiller's isolated DeepAgents harness and structured invocation."""
from __future__ import annotations

import asyncio
import json
from json import JSONDecodeError, JSONDecoder
from typing import Any, TypeVar

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.profiles.harness import harness_profiles
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

import ai
import domains

Schema = TypeVar("Schema", bound = BaseModel)


def build(
    *,
    model: Any,
    name: str,
    system_prompt: str,
    response_format: type[Schema],
    model_call_limit: int,
):
    if not isinstance(model, ChatOpenAI):
        raise TypeError("BD Ask agents require the configured ChatOpenAI model")
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("BD Ask agents require a named configured model")
    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(key, HarnessProfile(
        base_system_prompt = "",
        general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
    ))
    return create_deep_agent(
        model = model,
        tools = [],
        system_prompt = system_prompt,
        response_format = response_format,
        middleware = [
            ModelCallLimitMiddleware(run_limit = model_call_limit, exit_behavior = "end"),
            ai.bd.middlewares.middleware.ToolAllowlistMiddleware(frozenset()),
        ],
        permissions = [
            FilesystemPermission(operations = ["read"], paths = ["/**"], mode = "deny"),
            FilesystemPermission(operations = ["write"], paths = ["/**"], mode = "deny"),
        ],
        backend = StateBackend(),
        name = name,
    )


def _parse_result(result: Any, schema: type[Schema]) -> Schema | None:
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
    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role") or message.get("type")
        else:
            role = getattr(message, "type", None)
        if role not in {"assistant", "ai", "AIMessage"}:
            continue
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(content, list):
            content = "\n".join(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
        if not isinstance(content, str):
            continue
        for offset, character in enumerate(content):
            if character != "{":
                continue
            try:
                value, _ = JSONDecoder().raw_decode(content[offset:])
                return schema.model_validate(value)
            except (JSONDecodeError, ValidationError):
                continue
    return None


async def invoke(agent: Any, schema: type[Schema], payload: dict[str, Any]) -> Schema:
    text = json.dumps(payload, ensure_ascii = False, default = str)[:ai.bd.agents.ask.params.MAX_INPUT_CHARS]
    last_error: Exception | None = None
    for _ in range(ai.bd.agents.ask.params.MAX_RETRIES):
        try:
            result = await asyncio.wait_for(
                agent.ainvoke({"messages": [{"role": "user", "content": text}], "files": {}}),
                timeout = ai.bd.agents.ask.params.AGENT_TIMEOUT_S,
            )
            messages = result.get("messages") if isinstance(result, dict) else None
            if isinstance(messages, list):
                for message in messages:
                    if getattr(message, "usage_metadata", None):
                        await domains.settings.llm_counter.service.bump_response(message)
            parsed = _parse_result(result, schema)
            if parsed is not None:
                return parsed
            last_error = ValueError(f"BD Ask agent returned no valid {schema.__name__}")
        except Exception as error:  # noqa: BLE001 — retry is bounded and role-scoped.
            last_error = error
    raise ValueError(f"BD Ask agent invocation failed: {last_error}") from last_error
