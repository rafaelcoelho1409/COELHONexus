"""Shared DeepAgent construction and structured-response handling for YCS."""
from __future__ import annotations

import domains

from collections.abc import Callable
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

from . import params
from .. import middlewares


Schema = TypeVar("Schema", bound = BaseModel)


def build(
    *,
    model: Any,
    name: str,
    system_prompt: str,
    response_format: type[Schema],
    model_call_limit: int = 3,
):
    """Build a no-tool DeepAgent with a typed result and locked-down filesystem."""
    if not isinstance(model, ChatOpenAI):
        raise TypeError("YCS agents must use the configured ChatOpenAI model")
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError("YCS agents require a named configured model")

    # A DeepAgents harness is registered process-wide by model name. Clear the
    # entry before registering this role so another role cannot grant tools.
    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(
        key,
        HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
        ),
    )
    agent = create_deep_agent(
        model = model,
        tools = [],
        system_prompt = system_prompt,
        response_format = response_format,
        middleware = [
            ModelCallLimitMiddleware(run_limit = model_call_limit, exit_behavior = "end"),
            middlewares.middleware.CapacityWaitMiddleware(),
            middlewares.middleware.ToolAllowlistMiddleware(frozenset()),
        ],
        permissions = [
            FilesystemPermission(operations = ["read"], paths = ["/**"], mode = "deny"),
            FilesystemPermission(operations = ["write"], paths = ["/**"], mode = "deny"),
        ],
        backend = StateBackend(),
        name = name,
    )
    tool_node = agent.nodes.get("tools")
    available_tools = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available_tools, dict) or set(available_tools) - params.STACK_TOOLS:
        raise RuntimeError(f"DeepAgents exposed an unreviewed tool to {name}")
    return agent


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
    answer = structured_or_recovered(result, schema)
    if answer is None or (check is not None and not check(answer)):
        raise ValueError(f"the YCS agent ended without a valid {schema.__name__}")
    return answer
