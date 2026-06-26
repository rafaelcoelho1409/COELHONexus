"""TelemetryMiddleware — one OTel span per MCP tool call, dual-exported to Alloy+LangFuse."""
from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from infra.otel import get_tracer


logger = logging.getLogger(__name__)


class TelemetryMiddleware(Middleware):
    """OTel span wrapping every tool invocation."""

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ) -> Any:
        tool_name = _safe(lambda: context.message.name, "unknown")
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"mcp.tool.{tool_name}",
            kind=trace.SpanKind.SERVER,
        ) as span:
            span.set_attribute("coelho.langfuse.keep", True)
            span.set_attribute("coelho.langfuse.kind", "workflow_node")
            span.set_attribute("mcp.tool.name", tool_name)
            span.set_attribute("gen_ai.tool.name", tool_name)
            span.set_attribute("langfuse.observation.metadata.workflow", "rr_scan")
            span.set_attribute("langfuse.observation.metadata.node_name", tool_name)
            span.set_attribute("langfuse.observation.metadata.stage", "mcp_tool")
            arg_keys = _safe(
                lambda: ",".join(sorted((context.message.arguments or {}).keys())),
                "",
            )
            span.set_attribute("mcp.tool.args.keys", arg_keys)
            span.set_attribute("gen_ai.tool.argument_keys", arg_keys)

            try:
                result = await call_next(context)
                span.set_status(Status(StatusCode.OK))
                return result
            except Exception as e:
                span.record_exception(e)
                span.set_attribute("mcp.tool.error_type", type(e).__name__)
                span.set_attribute("gen_ai.tool.error_type", type(e).__name__)
                span.set_attribute(
                    "mcp.tool.error_msg", str(e).splitlines()[0][:240]
                )
                span.set_attribute(
                    "gen_ai.tool.error_msg", str(e).splitlines()[0][:240]
                )
                span.set_status(Status(StatusCode.ERROR, str(e)[:200]))
                raise


def _safe(fn, default):
    """Best-effort attribute access — never break the call because of a
    telemetry attribute extraction error."""
    try:
        return fn()
    except Exception:
        return default
