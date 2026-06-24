"""Pure attribute builders for gen_ai.* spans; service.py owns the span lifecycle."""
from __future__ import annotations

import json
from typing import Any

from .keys import (
    BANDIT_ATTEMPT,
    BANDIT_DD_PROCESS,
    BANDIT_DEPLOYMENT_ID,
    BANDIT_ERROR_CLASS,
    BANDIT_FALLBACK,
    BANDIT_LATENCY_S,
    BANDIT_PROVIDER,
    BANDIT_REWARD,
    BANDIT_SCHEMA_VALID,
    BANDIT_TOTAL_ATTEMPTS,
    GEN_AI_COMPLETION,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROMPT,
    GEN_AI_REQUEST_INPUT_COUNT,
    GEN_AI_REQUEST_INPUT_TYPE,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_EMBEDDING_VECTORS,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_RERANK_COUNT,
    GEN_AI_RESPONSE_RERANK_TOP_SCORE,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    OP_CHAT,
    OP_EMBEDDING,
    OP_RERANK,
    SYSTEM_LITELLM_ROTATOR,
)
from .params import (
    COMPLETION_TRUNCATE_CHARS,
    EMBEDDING_PREVIEW_CHARS,
    PROMPT_TRUNCATE_CHARS,
    RERANK_PREVIEW_CHARS,
)


def system_for_deployment(deployment_id: str | None) -> str:
    """LiteLLM deployment_id prefix → `gen_ai.system`; unprefixed → `litellm-rotator`."""
    if not deployment_id:
        return SYSTEM_LITELLM_ROTATOR
    prefix, sep, _ = deployment_id.partition("/")
    return prefix if sep else SYSTEM_LITELLM_ROTATOR


def provider_for_deployment(deployment_id: str | None) -> str:
    """Literal prefix (empty when unprefixed) for bandit.provider — no fallback sentinel."""
    if not deployment_id or "/" not in deployment_id:
        return ""
    return deployment_id.split("/", 1)[0]


def _truncate(s: str, cap: int) -> str:
    """Truncate to `cap` chars with a `…+Nb` suffix marking dropped bytes."""
    if cap <= 0 or not s:
        return ""
    if len(s) <= cap:
        return s
    return s[:cap] + f"…+{len(s) - cap}b"


def serialize_messages(messages: list[dict] | None, cap: int = PROMPT_TRUNCATE_CHARS) -> str:
    """OpenAI-style messages → truncated compact JSON for `gen_ai.prompt` (LangFuse generation input)."""
    if not messages:
        return ""
    try:
        raw = json.dumps(messages, ensure_ascii = False, separators = (",", ":"))
    except Exception:
        raw = str(messages)
    return _truncate(raw, cap)


def serialize_input_texts(
    texts: list[str] | None,
    cap: int = EMBEDDING_PREVIEW_CHARS,
) -> str:
    """First-text preview; full list would explode span size (count recorded separately)."""
    if not texts:
        return ""
    head = texts[0] if texts[0] else ""
    return _truncate(head, cap)


def build_chat_request_attrs(
    *,
    request_model: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        GEN_AI_SYSTEM:         system,
        GEN_AI_OPERATION_NAME: OP_CHAT,
        GEN_AI_REQUEST_MODEL:  request_model,
        GEN_AI_PROMPT:         serialize_messages(messages),
    }
    if temperature is not None:
        attrs[GEN_AI_REQUEST_TEMPERATURE] = float(temperature)
    if max_tokens is not None:
        attrs[GEN_AI_REQUEST_MAX_TOKENS] = int(max_tokens)
    if top_p is not None:
        attrs[GEN_AI_REQUEST_TOP_P] = float(top_p)
    return attrs


def build_embedding_request_attrs(
    *,
    request_model: str,
    texts: list[str],
    input_type: str | None = None,
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        GEN_AI_SYSTEM:               system,
        GEN_AI_OPERATION_NAME:       OP_EMBEDDING,
        GEN_AI_REQUEST_MODEL:        request_model,
        GEN_AI_REQUEST_INPUT_COUNT:  len(texts),
        GEN_AI_PROMPT:               serialize_input_texts(texts, EMBEDDING_PREVIEW_CHARS),
    }
    if input_type:
        attrs[GEN_AI_REQUEST_INPUT_TYPE] = input_type
    return attrs


def build_rerank_request_attrs(
    *,
    request_model: str,
    query: str,
    documents: list[str],
    system: str = SYSTEM_LITELLM_ROTATOR,
) -> dict[str, Any]:
    return {
        GEN_AI_SYSTEM:              system,
        GEN_AI_OPERATION_NAME:      OP_RERANK,
        GEN_AI_REQUEST_MODEL:       request_model,
        GEN_AI_REQUEST_INPUT_COUNT: len(documents),
        GEN_AI_PROMPT:              _truncate(query, RERANK_PREVIEW_CHARS),
    }


# LiteLLM response shapes vary by provider — these helpers tolerate dict OR object access.
def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _coerce_usage(usage: Any) -> dict:
    """Normalize LiteLLM `usage` to a plain dict."""
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump()
        except Exception:
            pass
    return getattr(usage, "__dict__", {}) or {}


def build_chat_response_attrs(response: Any) -> dict[str, Any]:
    """Returns an empty dict when the response shape is unrecognized."""
    attrs: dict[str, Any] = {}
    response_model = _get(response, "model")
    if response_model:
        attrs[GEN_AI_RESPONSE_MODEL] = str(response_model)
    response_id = _get(response, "id")
    if response_id:
        attrs[GEN_AI_RESPONSE_ID] = str(response_id)

    usage = _coerce_usage(_get(response, "usage"))
    inp = usage.get("prompt_tokens")
    out = usage.get("completion_tokens")
    if inp is not None:
        attrs[GEN_AI_USAGE_INPUT_TOKENS]        = int(inp)  # new OTel semconv
        attrs["gen_ai.usage.prompt_tokens"]     = int(inp)  # older convention (LangFuse fallback)
    if out is not None:
        attrs[GEN_AI_USAGE_OUTPUT_TOKENS]           = int(out)
        attrs["gen_ai.usage.completion_tokens"]     = int(out)
    if inp is not None or out is not None:
        total = int(inp or 0) + int(out or 0)
        attrs["gen_ai.usage.total_tokens"] = total
        # langfuse.observation.usage_details: JSON string LangFuse reads when
        # gen_ai.usage.* ingestion is broken (v3.x regression on some builds).
        attrs["langfuse.observation.usage_details"] = json.dumps({
            "input":  int(inp or 0),
            "output": int(out or 0),
            "total":  total,
            "unit":   "TOKENS",
        })

    choices = _get(response, "choices") or []
    if choices:
        first = choices[0]
        finish = _get(first, "finish_reason")
        if finish:
            attrs[GEN_AI_RESPONSE_FINISH_REASONS] = (str(finish),)
        message = _get(first, "message")
        content = _get(message, "content")
        if content:
            attrs[GEN_AI_COMPLETION] = _truncate(str(content), COMPLETION_TRUNCATE_CHARS)
    # All rotator arms are free-tier ($0). Set explicit zero cost so LangFuse
    # shows $0.0000 instead of null on cost columns.
    attrs["langfuse.observation.input_cost"]  = 0.0
    attrs["langfuse.observation.output_cost"] = 0.0
    attrs["langfuse.observation.total_cost"]  = 0.0
    return attrs


def build_embedding_response_attrs(response: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    response_model = _get(response, "model")
    if response_model:
        attrs[GEN_AI_RESPONSE_MODEL] = str(response_model)
    usage = _coerce_usage(_get(response, "usage"))
    if usage.get("prompt_tokens") is not None:
        inp = int(usage["prompt_tokens"])
        attrs[GEN_AI_USAGE_INPUT_TOKENS]            = inp
        attrs["gen_ai.usage.prompt_tokens"]         = inp
        attrs["gen_ai.usage.total_tokens"]          = inp
        attrs["langfuse.observation.usage_details"] = json.dumps({
            "input": inp, "output": 0, "total": inp, "unit": "TOKENS",
        })
    data = _get(response, "data") or []
    if data:
        attrs[GEN_AI_RESPONSE_EMBEDDING_VECTORS] = len(data)
    return attrs


def build_rerank_response_attrs(
    rankings: list[tuple[int, float]] | None,
) -> dict[str, Any]:
    if not rankings:
        return {GEN_AI_RESPONSE_RERANK_COUNT: 0}
    return {
        GEN_AI_RESPONSE_RERANK_COUNT:     len(rankings),
        GEN_AI_RESPONSE_RERANK_TOP_SCORE: float(rankings[0][1]),
    }


def build_bandit_attempt_attrs(
    *,
    deployment_id: str,
    attempt: int,
    dd_process: str | None = None,
    latency_s: float | None = None,
    reward: float | None = None,
    error_class: str | None = None,
    schema_valid: bool | None = None,
) -> dict[str, Any]:
    """Bandit-specific axes only (arm, attempt, reward, error); caller adds gen_ai.* request attrs."""
    attrs: dict[str, Any] = {
        BANDIT_DEPLOYMENT_ID: deployment_id,
        BANDIT_PROVIDER:      provider_for_deployment(deployment_id),
        BANDIT_ATTEMPT:       int(attempt),
    }
    if dd_process:
        attrs[BANDIT_DD_PROCESS] = dd_process
    if latency_s is not None:
        attrs[BANDIT_LATENCY_S] = float(latency_s)
    if reward is not None:
        attrs[BANDIT_REWARD] = float(reward)
    if error_class:
        attrs[BANDIT_ERROR_CLASS] = error_class
    if schema_valid is not None:
        attrs[BANDIT_SCHEMA_VALID] = bool(schema_valid)
    return attrs


def build_bandit_cascade_attrs(
    *,
    dd_process: str,
    total_attempts: int | None = None,
    fallback: str | None = None,
) -> dict[str, Any]:
    """Parent cascade-span attrs; total_attempts + fallback updated at end."""
    attrs: dict[str, Any] = {BANDIT_DD_PROCESS: dd_process}
    if total_attempts is not None:
        attrs[BANDIT_TOTAL_ATTEMPTS] = int(total_attempts)
    if fallback:
        attrs[BANDIT_FALLBACK] = fallback
    return attrs
