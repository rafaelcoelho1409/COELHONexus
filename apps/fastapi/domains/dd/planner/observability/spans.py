"""OTel span helper for planner nodes.

`@traced("name")` wraps any async node coroutine so its execution
becomes a top-level OTel span. The span is emitted by the global
tracer provider set up in `services.llm.otel_setup.init_otel()` —
which dual-exports to Alloy (gRPC) AND LangFuse (OTLP/HTTP).

In LangFuse, each wrapped node shows up as its own observation under
the trace whose id matches `state["thread_id"]`. That's how we get
per-substep visibility instead of one big "planner" blob.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable

from core.otel_setup import get_tracer


logger = logging.getLogger(__name__)


def traced(name: str) -> Callable:
    """Decorate `async def node(state) -> dict` so each invocation is a
    top-level OTel span. The state's `thread_id` is attached as a span
    attribute so LangFuse can group spans into a trace.
    """
    def decorator(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs) -> dict:
            tracer = get_tracer()
            if tracer is None:
                # OTel not initialized (e.g. local dev without env). Run
                # the node anyway so the graph stays usable.
                return await fn(state, *args, **kwargs)
            attrs = {
                "planner.node": name,
                "planner.thread_id": state.get("thread_id") or "",
                "planner.framework_slug": state.get("framework_slug") or "",
            }
            with tracer.start_as_current_span(f"planner/{name}", attributes=attrs) as span:
                try:
                    result = await fn(state, *args, **kwargs)
                    span.set_attribute("planner.ok", True)
                    return result
                except Exception as e:
                    span.set_attribute("planner.ok", False)
                    span.set_attribute("planner.error_type", type(e).__name__)
                    span.set_attribute("planner.error_message", str(e)[:200])
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator
