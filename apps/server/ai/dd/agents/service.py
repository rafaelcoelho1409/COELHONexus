"""What every Docs Distiller deep agent shares: the resilience stack when it is built, and how it is asked."""
from __future__ import annotations
import domains
from . import domain, params
from .. import middlewares

from collections.abc import Callable
from typing import Any, TypeVar

from deepagents.backends.utils import create_file_data
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware, ToolCallLimitMiddleware, ToolErrorMiddleware
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
        middlewares.middleware.CapacityWaitMiddleware(),
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
    agent: Any,
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
    """Run `agent` on `payload` and return its answer as `schema`.

    `files` is the agent's virtual filesystem for this call: its skill files (`skills.service.files_for`) and the files it reads. The
    answer is the agent's structured response, or the first JSON object of its last message that fits the schema (some
    models close with a fenced JSON block instead of calling the structured-output tool). Transient provider failures and
    unusable answers are asked again, `attempts` times in all (`check` decides whether a valid answer is usable); a run that
    never produces an answer raises ValueError."""
    request = {
        "messages": [{"role": "user", "content": payload}],
        "files": files or {},
    }

    def usable(value: Any) -> bool:
        parsed = domain.structured_or_recovered(value, schema)
        return parsed is not None and (check is None or check(parsed))

    result = await domains.settings.chat.service.resilient_ainvoke(
        lambda: agent.ainvoke(request, config = request_config(config)),
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
