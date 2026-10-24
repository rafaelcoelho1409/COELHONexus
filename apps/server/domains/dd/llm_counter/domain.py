"""Pure LLM-response helpers for DD usage counters — no I/O. Token parsing is shared: `domains.settings.llm_usage.domain.extract_usage`."""
from __future__ import annotations

from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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
