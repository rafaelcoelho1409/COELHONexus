"""OTel span helpers for planner nodes.

`@traced("name")` wraps any async node coroutine so its execution becomes
a top-level OTel span. The span is emitted by the global tracer provider
set up in `core.otel.init_otel()` — which dual-exports to Alloy
(gRPC) AND LangFuse (OTLP/HTTP).

In LangFuse, each wrapped node shows up as its own observation under the
trace whose id matches `state["thread_id"]`. That's how we get per-substep
visibility instead of one big "planner" blob.

`attach_span_attrs(prefix, attrs)` attaches the node's stats dict to the
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
    """Decorate `async def node(state) -> dict` so each invocation is a
    top-level OTel span. The state's `thread_id` is attached as a span
    attribute so LangFuse can group spans into a trace."""
    def decorator(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs) -> dict:
            tracer = get_tracer()
            if tracer is None:
                # OTel not initialized (e.g. local dev without env).
                # Run the node anyway so the graph stays usable.
                return await fn(state, *args, **kwargs)
            attrs = {
                "planner.node": name,
                "planner.thread_id": state.get("thread_id") or "",
                "planner.framework_slug": state.get("framework_slug") or "",
            }
            with tracer.start_as_current_span(
                f"planner/{name}", attributes = attrs,
            ) as span:
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


def attach_span_attrs(prefix: str, attrs: dict) -> None:
    """Set namespaced attributes on the currently-active OTel span. No-op
    if OTel isn't initialized. None values are skipped — the OTel
    backend rejects them."""
    try:
        span = _otel_trace.get_current_span()
        for k, v in attrs.items():
            if v is None:
                continue
            span.set_attribute(f"{prefix}.{k}", v)
    except Exception:
        pass
