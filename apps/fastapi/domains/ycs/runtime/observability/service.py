"""OTel span helpers for YCS RAG nodes — mirrors DD planner/synth
observability shape.

`@traced("name")` wraps any async node coroutine so its execution becomes
a top-level OTel span. The span is emitted by the global tracer provider
configured by `infra.otel.init_otel()` — which dual-exports to Alloy
(gRPC → Tempo) AND LangFuse v3 (HTTP → LLM observations).

In LangFuse, each wrapped node shows up as its own observation under the
trace whose id matches `state["thread_id"]` (when present). That's how we
get per-node visibility on the adaptive + standard RAG sub-graphs instead
of one opaque pipeline-task blob from CeleryInstrumentor.

`attach_span_attrs(prefix, attrs)` attaches a node's stats dict to the
currently-active span so the LangFuse observation carries the per-node
metrics tree (retrieved doc count, grader scores, hallucination flag, etc.).
"""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from opentelemetry import trace as _otel_trace

from infra.otel import get_tracer


logger = logging.getLogger(__name__)


def traced(name: str) -> Callable:
    """Decorate `async def node(state, *args, **kwargs) -> dict` so each
    invocation is a top-level OTel span. State's `thread_id` / `question` /
    channel context are attached as span attributes so LangFuse can group
    spans into a trace and Tempo can filter by user query."""
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
