"""OTel span helpers for YCS RAG nodes — mirrors DD planner/synth observability.

`@traced("name")` wraps a node into a top-level OTel span (dual-export: Alloy gRPC + LangFuse).
`attach_span_attrs(prefix, attrs)` attaches a stats dict to the currently-active span.
"""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from opentelemetry import trace as _otel_trace

from infra.otel import get_tracer


logger = logging.getLogger(__name__)


def traced(name: str) -> Callable:
    """Wrap an async node so each invocation becomes a top-level OTel span."""
    def decorator(fn: Callable[..., Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs) -> dict:
            tracer = get_tracer()
            if tracer is None:
                return await fn(state, *args, **kwargs)
            attrs = {
                "coelho.langfuse.keep": True,
                "coelho.langfuse.kind": "workflow_node",
                "ycs.node":      name,
                "ycs.thread_id": state.get("thread_id") or "",
                "ycs.question":  (state.get("question") or "")[:200],
                "langfuse.observation.metadata.workflow": "ycs_ask",
                "langfuse.observation.metadata.node_name": name,
                "langfuse.observation.metadata.stage": "ycs",
            }
            with tracer.start_as_current_span(
                f"ycs.node.{name}", attributes = attrs,
            ) as span:
                try:
                    result = await fn(state, *args, **kwargs)
                    span.set_attribute("ycs.ok", True)
                    return result
                except Exception as e:
                    span.set_attribute("ycs.ok", False)
                    span.set_attribute("ycs.error_type", type(e).__name__)
                    span.set_attribute("ycs.error_message", str(e)[:200])
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator


def attach_span_attrs(prefix: str, attrs: dict) -> None:
    """Set namespaced attributes on the current OTel span; no-op if uninitialized; None values skipped."""
    try:
        span = _otel_trace.get_current_span()
        for k, v in attrs.items():
            if v is None:
                continue
            span.set_attribute(f"{prefix}.{k}", v)
    except Exception:
        pass
