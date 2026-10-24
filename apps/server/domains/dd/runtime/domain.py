"""Pure LLM-response parsing for DD usage counters — no I/O."""
from __future__ import annotations

from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _coerce_usage(usage: Any) -> dict:
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


def extract_usage(response: Any) -> tuple[int, int, int]:
    """Return input/output/reasoning tokens from a LiteLLM response."""
    usage = _coerce_usage(_get(response, "usage"))
    tokens_in = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    tokens_out = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    details = (
        usage.get("completion_tokens_details")
        or usage.get("output_tokens_details")
        or {}
    )
    if not isinstance(details, dict):
        details = _coerce_usage(details)
    reasoning = int(
        usage.get("reasoning_tokens")
        or details.get("reasoning_tokens")
        or 0
    )
    return max(0, tokens_in), max(0, tokens_out), max(0, reasoning)


def _model_from_response(response: Any, fallback: str | None = None) -> str:
    model = _get(response, "model")
    if isinstance(fallback, str) and "/" in fallback:
        return fallback
    if isinstance(model, str) and model:
        return model
    hidden = _get(response, "_hidden_params")
    if isinstance(hidden, dict):
        for key in ("model_id", "model"):
            val = hidden.get(key)
            if isinstance(val, str) and val:
                return val
    return fallback or "unknown"
