"""OTel span helper for synth nodes — mirrors planner/observability/spans.

Each `@traced("name")` wrapper makes the node a top-level OTel span so
LangFuse groups it under the trace whose id matches `state["thread_id"]`.
Span attributes capture framework_slug + chapter_id so the per-chapter
Gantt slice is easy to navigate.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable

from core.otel_setup import get_tracer


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
            with tracer.start_as_current_span(f"synth/{name}", attributes=attrs) as span:
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
