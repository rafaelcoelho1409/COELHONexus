"""The one place a Docs Distiller deep agent is built and asked: model, harness profile, file permissions, resilience stack and the unreviewed-tool guard."""
from __future__ import annotations
import domains
from . import domain, entities, params
from .. import middleware, skills

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from deepagents.profiles.harness import harness_profiles
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware, ToolCallLimitMiddleware, ToolErrorMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel


Schema = TypeVar("Schema", bound = BaseModel)


def resilience(*, model_calls: int, with_tools: bool) -> list[Any]:
    """The middleware every agent carries, in order (the first is the outermost): transient provider failures are retried, a
    Rotator capacity wait is waited out, a run that never reaches an answer stops after `model_calls` calls, and a tool failure
    reaches the model as a message instead of ending the run."""
    stack: list[Any] = [
        ModelRetryMiddleware(
            max_retries = params.MODEL_RETRIES,
            retry_on = domain.is_transient,
            on_failure = "error",
            initial_delay = params.MODEL_RETRY_DELAY_S,
            backoff_factor = params.MODEL_RETRY_BACKOFF,
            max_delay = params.MODEL_RETRY_MAX_DELAY_S,
        ),
        middleware.service.CapacityWaitMiddleware(),
        ModelCallLimitMiddleware(run_limit = model_calls, exit_behavior = "end"),
    ]
    if with_tools:
        stack += [
            ToolCallLimitMiddleware(run_limit = params.TOOL_CALL_LIMIT, exit_behavior = "continue"),
            ToolErrorMiddleware(
                lambda error, request: f"`{request.tool_call['name']}` failed with {type(error).__name__}: {str(error)[:params.TOOL_ERROR_CHARS]}",
            ),
        ]
    return stack


def isolate_harness_profile(model_name: str) -> None:
    """Make the profile of the Rotator model the same for every role: no base prompt, no general-purpose subagent, no excluded tool.

    Deep Agents keys the profile by the model, registrations are additive, and their excluded tools union. Every role uses the same
    Rotator model, so an exclusion registered once (by a role, or by other code in the process) would silently hide those tools from
    every agent built after it in the same worker process. The key is cleared before it is set, and what a role may use is limited by
    its own `middleware.service.ToolAllowlistMiddleware`. The profile is read when `create_deep_agent` runs, so call this right before."""
    key = f"openai:{model_name}"
    harness_profiles._HARNESS_PROFILES.pop(key, None)
    register_harness_profile(
        key,
        HarnessProfile(
            base_system_prompt = "",
            general_purpose_subagent = GeneralPurposeSubagentProfile(enabled = False),
        ),
    )


def subagent(
    name: str,
    *,
    description: str,
    system_prompt: str,
    skill_role: str | None = None,
    tools: Sequence[Any] = (),
    allowed_tools: frozenset[str] = params.READ_TOOLS,
    model_calls: int = params.MODEL_CALL_LIMIT,
) -> dict[str, Any]:
    """A declarative subagent for `build(subagents=...)`. It sees only the text it is sent and does not inherit the parent's
    skills or middleware, so it lists its own skill source, carries its own resilience stack and sees its own tools: `tools` plus
    the stack tools in `allowed_tools` (the read tools by default)."""
    custom_names = {getattr(tool, "name", None) or getattr(tool, "__name__", "") for tool in tools}
    spec: dict[str, Any] = {
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "tools": list(tools),
        "middleware": [
            *resilience(model_calls = model_calls, with_tools = True),
            middleware.service.ToolAllowlistMiddleware(allowed_tools | custom_names),
        ],
    }
    if skill_role:
        spec["skills"] = [skills.domain.source_for(skill_role)]
    return spec


def build(
    name: str,
    *,
    system_prompt: str,
    config: entities.ModelConfig,
    rotator_task: str,
    response_format: type[BaseModel] | None = None,
    skill_role: str | None = None,
    subagent_skill_roles: Sequence[str] = (),
    allowed_tools: frozenset[str] = frozenset(),
    tools: Sequence[Any] = (),
    subagents: Sequence[Any] = (),
    backend: Any = None,
    read_paths: Sequence[str] = (),
    write_paths: Sequence[str] = (),
    middleware_extra: Sequence[Any] = (),
    model: BaseChatModel | None = None,
    model_calls: int = params.MODEL_CALL_LIMIT,
) -> entities.Role:
    """Build one Docs Distiller deep agent.

    `allowed_tools` names the stack tools (`params.STACK_TOOLS`) the role may use; the model is shown no other one
    (`ToolAllowlistMiddleware`), and `tools` are the role's own. `read_paths` / `write_paths` are the only places its file tools reach, `/skills/**`
    included when it has a skill. A model call that fails for a transient reason is retried, a Rotator capacity wait is waited
    out, and a run that never reaches an answer stops after `model_calls` calls."""
    if model is None:
        model = domains.settings.chat.service.build_chat_model(
            timeout_s = config.timeout_s,
            max_tokens = config.max_output_tokens,
            temperature = config.temperature,
            max_retries = config.max_retries,
            rotator_task = rotator_task,
        )
    model_name = getattr(model, "model_name", None)
    if not isinstance(model_name, str) or not model_name or ":" in model_name:
        raise ValueError(f"the {name} agent requires a named OpenAI-compatible Rotator model")

    unknown = allowed_tools - params.STACK_TOOLS
    if unknown:
        raise ValueError(f"the {name} agent allows tools the stack does not have: {sorted(unknown)}")

    readable = [*read_paths, *([f"{skills.params.VIRTUAL_ROOT}/**"] if skill_role else [])]
    permissions = [
        *[FilesystemPermission(operations = ["read"], paths = [path], mode = "allow") for path in readable],
        FilesystemPermission(operations = ["read"], paths = ["/**"], mode = "deny"),
        *[FilesystemPermission(operations = ["write"], paths = [path], mode = "allow") for path in write_paths],
        FilesystemPermission(operations = ["write"], paths = ["/**"], mode = "deny"),
    ]

    custom = [tool for tool in tools]
    custom_names = {getattr(tool, "name", None) or getattr(tool, "__name__", "") for tool in custom}
    stack = resilience(model_calls = model_calls, with_tools = bool(custom or allowed_tools & params.READ_TOOLS))
    stack.append(middleware.service.ToolAllowlistMiddleware(allowed_tools | custom_names))

    isolate_harness_profile(model_name)
    agent = create_deep_agent(
        model = model,
        tools = custom,
        system_prompt = system_prompt,
        response_format = response_format,
        permissions = permissions,
        backend = backend if backend is not None else StateBackend(),
        skills = [skills.domain.source_for(skill_role)] if skill_role else None,
        subagents = list(subagents) or None,
        middleware = [*stack, *middleware_extra],
        name = name,
    )

    tool_node = agent.nodes.get("tools")
    available = getattr(getattr(tool_node, "bound", None), "_tools_by_name", None)
    if not isinstance(available, dict):
        raise RuntimeError(f"DeepAgents did not expose the {name} agent's tools for review")
    unreviewed = set(available) - params.STACK_TOOLS - custom_names
    if unreviewed:
        raise RuntimeError(f"DeepAgents exposed unreviewed tools to the {name} agent: {sorted(unreviewed)}")
    files: dict[str, Any] = {}
    for role in [skill_role, *subagent_skill_roles] if skill_role else [*subagent_skill_roles]:
        files.update(skills.service.files_for(role))
    return entities.Role(agent = agent, files = files)


def file(content: str) -> dict[str, Any]:
    """A text file in the agent's virtual filesystem, for `ask(files=...)`."""
    return create_file_data(content)


def request_config(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The invoke config every agent call carries: usage is counted on the current (run, node) attribution."""
    callbacks = [domains.settings.llm_counter.service.LLMCounterCallback()]
    merged = dict(extra or {})
    merged["callbacks"] = [*(merged.get("callbacks") or []), *callbacks]
    return merged


async def ask(
    role: entities.Role,
    schema: type[Schema],
    payload: str,
    *,
    attempts: int,
    wait_base_s: float,
    files: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    check: Callable[[Schema], bool] | None = None,
    meta: dict[str, Any] | None = None,
) -> Schema:
    """Run the role's agent on `payload` and return its answer as `schema`.

    The answer is the agent's structured response, or the first JSON object of its last message that fits the schema (some
    models close with a fenced JSON block instead of calling the structured-output tool). Transient provider failures and
    unusable answers are asked again, `attempts` times in all (`check` decides whether a valid answer is usable); a run that
    never produces an answer raises ValueError."""
    request = {
        "messages": [{"role": "user", "content": payload}],
        "files": {**role.files, **(files or {})},
    }

    def usable(value: Any) -> bool:
        parsed = domain.structured_or_recovered(value, schema)
        return parsed is not None and (check is None or check(parsed))

    result = await domains.settings.chat.service.resilient_ainvoke(
        lambda: role.agent.ainvoke(request, config = request_config(config)),
        attempts = attempts,
        wait_base_s = wait_base_s,
        accept = usable,
    )
    if meta is not None:
        meta.update(domain.rotator_meta(result))
    answer = domain.structured_or_recovered(result, schema)
    if answer is None:
        raise ValueError(f"the {schema.__name__} agent ended {attempts} times without a valid {schema.__name__}")
    return answer
