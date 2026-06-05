"""OTel span helpers for synth nodes — mirrors planner/observability/service.

Each `@traced("name")` wrapper makes the node a top-level OTel span so
LangFuse groups it under the trace whose id matches `state["thread_id"]`.
Span attributes capture framework_slug + chapter_id so the per-chapter
Gantt slice is easy to navigate.

`attach_span_attrs(prefix, attrs)` attaches a node's stats dict to the
currently-active span so the LangFuse observation carries the per-substep
metrics tree.
"""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from opentelemetry import trace as _otel_trace

from core.otel import get_tracer


logger = logging.getLogger(__name__)


def traced(name: str) -> Callable:
    """Decorator: turn an async node coroutine into a top-level OTel span."""
    def decorator(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs) -> dict:
            tracer = get_tracer()
            if tracer is None:
                return await fn(state, *args, **kwargs)
            attrs = {
                "synth.node":           name,
                "synth.thread_id":      state.get("thread_id") or "",
                "synth.framework_slug": state.get("framework_slug") or "",
                "synth.chapter_id":     state.get("chapter_id") or "",
            }
            with tracer.start_as_current_span(
                f"synth/{name}", attributes = attrs,
            ) as span:
                try:
                    result = await fn(state, *args, **kwargs)
                    span.set_attribute("synth.ok", True)
                    return result
                except Exception as e:
                    span.set_attribute("synth.ok", False)
                    span.set_attribute("synth.error_type", type(e).__name__)
                    span.set_attribute("synth.error_message", str(e)[:200])
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator


def attach_span_attrs(prefix: str, attrs: dict) -> None:
    """Set namespaced attributes on the currently-active OTel span. No-op
    if OTel isn't initialized. None values are skipped — the OTel backend
    rejects them."""
    try:
        span = _otel_trace.get_current_span()
        for k, v in attrs.items():
            if v is None:
                continue
            span.set_attribute(f"{prefix}.{k}", v)
    except Exception:
        pass
