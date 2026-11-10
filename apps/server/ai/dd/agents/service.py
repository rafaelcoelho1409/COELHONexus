"""What every Docs Distiller deep agent shares: the resilience stack when it is built, and how it is asked."""
from __future__ import annotations
import domains
from . import domain, params
from .. import middlewares

from collections.abc import Callable
import json
from typing import Any, TypeVar

from deepagents.backends.utils import create_file_data
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware, ToolCallLimitMiddleware, ToolErrorMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel


Schema = TypeVar("Schema", bound = BaseModel)


def _safe_error_summary(error: Exception) -> str:
    """Expose provider status codes in diagnostics without persisting response bodies or user content."""
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(error, "response", None), "status_code", None)
    detail = type(error).__name__
    if isinstance(status, int):
        detail += f", status={status}"
    return detail


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
    merged = dict(extra or {})
    callbacks = list(merged.get("callbacks") or [])
    callback_type = domains.settings.llm_counter.service.LLMCounterCallback
    if not any(isinstance(callback, callback_type) for callback in callbacks):
        callbacks.append(callback_type())
    merged["callbacks"] = callbacks
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
    failures: list[str] = []

    def usable(value: Any) -> bool:
        parsed = domain.structured_or_recovered(value, schema)
        if parsed is None:
            failures.append(domain.structured_failure_reason(value, schema))
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
        detail = failures[-1] if failures else "no parse diagnostic was available"
        raise ValueError(
            f"the {schema.__name__} agent ended {attempts} times without a valid {schema.__name__}; {detail}"
        )
    return answer


async def ask_chat_model(
    model: Any,
    schema: type[Schema],
    system_prompt: str,
    payload: str,
    *,
    attempts: int,
    wait_base_s: float,
    config: dict[str, Any] | None = None,
    recover_json_after_transient_error: bool = True,
) -> Schema:
    """Ask a tool-free model role for a schema, then make bounded JSON-text recovery attempts.

    This path is for tasks that do not need an agent loop or executable tools. It keeps the structured call's raw
    message so function-call arguments and provider finish metadata remain inspectable when parsing fails.
    """
    messages = [SystemMessage(content = system_prompt), HumanMessage(content = payload)]
    failures: list[str] = []

    def usable(result: Any) -> bool:
        parsed = domain.structured_or_recovered(result, schema)
        if parsed is None:
            failures.append(domain.structured_failure_reason(result, schema))
        return parsed is not None

    structured_result: Any = None
    structured_error: Exception | None = None
    try:
        structured = model.with_structured_output(schema, method = "function_calling", include_raw = True)
        structured_result = await domains.settings.chat.service.resilient_ainvoke(
            lambda: structured.ainvoke(messages, config = request_config(config)),
            attempts = attempts,
            wait_base_s = wait_base_s,
            accept = usable,
        )
        answer = domain.structured_or_recovered(structured_result, schema)
        if answer is not None:
            return answer
        failures.append(domain.structured_failure_reason(structured_result, schema))
    except Exception as error:
        # Keep provider response bodies and source text out of planner health metadata.
        structured_error = error
        failures.append(f"function-call request failed ({_safe_error_summary(error)})")

    # A second prompt with a different response format cannot repair a timeout,
    # rate limit or provider outage. Expensive callers can fail fast here while
    # retaining JSON-text recovery for a successful but malformed response.
    if (
        structured_error is not None
        and not recover_json_after_transient_error
        and domain.is_transient(structured_error)
    ):
        raise structured_error

    fallback_instruction = (
        "Return one JSON object only, with no Markdown fence or commentary. It must match this JSON Schema exactly:\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii = False)
    )
    try:
        recovery_messages = [*messages, HumanMessage(content = fallback_instruction)]
        raw = await domains.settings.chat.service.resilient_ainvoke(
            lambda: model.ainvoke(recovery_messages, config = request_config(config)),
            attempts = attempts,
            wait_base_s = wait_base_s,
            accept = lambda value: usable({"raw": value}),
        )
        fallback_result = {"raw": raw}
        answer = domain.structured_or_recovered(fallback_result, schema)
        if answer is not None:
            return answer
        failures.append("JSON-text recovery: " + domain.structured_failure_reason(fallback_result, schema))
    except Exception as error:
        failures.append(f"JSON-text recovery failed ({_safe_error_summary(error)})")

    detail = failures[-1] if failures else "no parse diagnostic was available"
    first = failures[0] if len(failures) > 1 else "structured response was unusable"
    raise ValueError(
        f"the {schema.__name__} model produced no valid answer after up to {attempts} structured and "
        f"{attempts} JSON-text recovery attempt(s); "
        f"{first}; {detail}"
    )
