"""Shared `execute_tool` span decorator for every `@tool` function under
`domains.rr.agent.tools.*` — OTel GenAI semantic conventions (Development
stability): https://github.com/open-telemetry/semantic-conventions-genai
(`docs/gen-ai/gen-ai-spans.md`, "Execute tool span").

Apply directly under `@tool` (`@tool` must stay the OUTERMOST/topmost
decorator — it introspects the function's signature/docstring to build the
tool schema; `functools.wraps` below preserves `__name__`/`__doc__`/
`__annotations__`/`__wrapped__` so that introspection still sees the real
function, not this wrapper's `*args, **kwargs`):

    @tool
    @traced_tool
    async def discover_arxiv(query: str) -> str:
        ...
"""
from __future__ import annotations

import asyncio
import functools

import infra
from opentelemetry import trace


def _span_attrs(tool_name: str) -> dict:
    return {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name":      tool_name,
        "gen_ai.tool.type":      "function",
        "gen_ai.agent.name":     "rr-orchestrator",
        "coelho.langfuse.keep":  True,
    }


def traced_tool(fn):
    """Wrap a LangChain `@tool` function (sync or async) in an
    `execute_tool {name}` span."""
    tool_name = fn.__name__

    if asyncio.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _async_wrapper(*args, **kwargs):
            with infra.otel.service.get_tracer().start_as_current_span(
                f"execute_tool {tool_name}",
                kind       = trace.SpanKind.INTERNAL,
                attributes = _span_attrs(tool_name),
            ) as span:
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    span.set_attribute("error.type", type(e).__name__)
                    span.record_exception(e)
                    raise
        return _async_wrapper

    @functools.wraps(fn)
    def _sync_wrapper(*args, **kwargs):
        with infra.otel.service.get_tracer().start_as_current_span(
            f"execute_tool {tool_name}",
            kind       = trace.SpanKind.INTERNAL,
            attributes = _span_attrs(tool_name),
        ) as span:
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                span.set_attribute("error.type", type(e).__name__)
                span.record_exception(e)
                raise
    return _sync_wrapper
