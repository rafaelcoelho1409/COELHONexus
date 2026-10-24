"""llm_usage domain — one pure parser for the token usage and model of an LLM response.

Reads every shape the codebase sees: a LangChain `LLMResult` (the `on_llm_end` callback payload —
LangChain's native `AIMessage.usage_metadata` first, then `llm_output`) and a raw provider /
LiteLLM response (`.usage` + `.model`). DD, RR and YCS each keep their own counters and Redis
shapes but all parse responses here. No I/O."""
from __future__ import annotations
from . import entities

from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    return getattr(obj, "__dict__", {}) or {}


def _tokens_from_usage(usage: dict) -> tuple[int, int, int]:
    """(in, out, reasoning) from an OpenAI-style usage dict (`prompt_tokens`/`input_tokens`, ...)."""
    tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    details = _as_dict(usage.get("completion_tokens_details") or usage.get("output_tokens_details"))
    reasoning = int(usage.get("reasoning_tokens") or details.get("reasoning_tokens") or 0)
    return tokens_in, tokens_out, reasoning


def pick_first_real_model(*candidates: Any) -> str | None:
    """The first non-empty model string among `candidates`."""
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None


def extract_usage(response: Any) -> entities.Usage:
    """Parse `response` into a `Usage`. Later, more specific sources win when they carry a number:
    raw `.usage`, then `llm_output` (`token_usage` / `usage`), then the first generation's
    `AIMessage.usage_metadata`. The model is the first real name among those sources, in that order."""
    tokens_in = tokens_out = reasoning = 0
    models: list[Any] = []

    raw_usage = _as_dict(_get(response, "usage"))
    if raw_usage:
        tokens_in, tokens_out, reasoning = _tokens_from_usage(raw_usage)
    models.append(_get(response, "model"))

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        models += [llm_output.get("model_name"), llm_output.get("model"), llm_output.get("model_id")]
        usage = _as_dict(llm_output.get("token_usage") or llm_output.get("usage"))
        if usage:
            t_in, t_out, t_reason = _tokens_from_usage(usage)
            tokens_in, tokens_out, reasoning = t_in or tokens_in, t_out or tokens_out, t_reason or reasoning

    generations = getattr(response, "generations", None) or []
    if generations and generations[0]:
        message = getattr(generations[0][0], "message", None)
        if message is not None:
            meta = getattr(message, "usage_metadata", None)
            if isinstance(meta, dict):
                tokens_in = int(meta.get("input_tokens") or tokens_in)
                tokens_out = int(meta.get("output_tokens") or tokens_out)
                out_details = meta.get("output_token_details")
                if isinstance(out_details, dict):
                    reasoning = int(out_details.get("reasoning") or reasoning)
            response_meta = getattr(message, "response_metadata", None) or {}
            if isinstance(response_meta, dict):
                models += [
                    response_meta.get("model_name"), response_meta.get("model"),
                    response_meta.get("ls_model_name"), response_meta.get("ls_provider"),
                ]
                raw = response_meta.get("response") or response_meta.get("model_extra") or {}
                if isinstance(raw, dict):
                    models += [raw.get("model"), raw.get("model_name")]
            extra = getattr(message, "additional_kwargs", None)
            if isinstance(extra, dict):
                models += [extra.get("model"), extra.get("model_name")]

    return entities.Usage(
        model = pick_first_real_model(*models),
        tokens_in = max(0, tokens_in),
        tokens_out = max(0, tokens_out),
        reasoning_tokens = max(0, reasoning),
    )
