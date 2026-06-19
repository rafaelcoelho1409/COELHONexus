"""LiteLLM success callbacks — extras beyond the gen_ai.* span emitted
by our own `genai_completion_span` wrapper. The `langfuse_otel` builtin
no-ops under Router (proxy required), so we wire this thin callback to
attach per-call cost as a LangFuse score on whichever trace contains
the completion.

The callback receives LiteLLM's standard signature:
    cost_callback(kwargs, completion_response, start_time, end_time)

It runs synchronously after every successful chat completion. Failures
are swallowed — never block the LLM path because of telemetry.
"""
from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


def cost_callback(kwargs, completion_response, start_time, end_time) -> None:  # noqa: ARG001
    """Record `cost.usd` score on the active trace + ignore $0 (free-tier)."""
    try:
        import litellm
    except Exception:
        return
    try:
        cost = getattr(completion_response, "response_cost", None)
        if cost is None:
            try:
                cost = litellm.completion_cost(
                    completion_response = completion_response,
                )
            except Exception:
                cost = None
        if cost is None or float(cost) <= 0.0:
            return  # free-tier or unknown pricing — no point recording
        model = (
            (kwargs or {}).get("model")
            or getattr(completion_response, "model", "unknown")
            or "unknown"
        )
        from infra.langfuse.scores import record_score
        record_score(
            "cost.usd",
            float(cost),
            comment = f"model={model}",
        )
    except Exception as e:
        logger.debug(
            f"[litellm-cost-callback] dropped: {type(e).__name__}: {e}"
        )
