"""COELHO Nexus — FastMCP server. Registers all domain tools and exposes the Streamable-HTTP ASGI app."""
import logging
import os


_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"
)


def _install_log_record_defaults() -> None:
    old_factory = logging.getLogRecordFactory()
    if getattr(old_factory, "_coelho_otel_defaults", False):
        return

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.otelTraceID = getattr(record, "otelTraceID", "0")
        record.otelSpanID = getattr(record, "otelSpanID", "0")
        return record

    record_factory._coelho_otel_defaults = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(record_factory)


_install_log_record_defaults()
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from domains.rr import server as rr
from infra.credentials import inject_user_keys_into_env
from infra.otel import init_otel
from middleware.ratelimit import RateLimitMiddleware
from middleware.telemetry import TelemetryMiddleware

# Must run before tool imports — tools read os.environ at import time for rate-limit config.
inject_user_keys_into_env(("SEMANTIC_SCHOLAR_API_KEY",))

# Must run before tool imports — TelemetryMiddleware needs the exporter pipeline already wired.
init_otel()

mcp = FastMCP("coelhonexus-mcp")

# TelemetryMiddleware wraps RateLimitMiddleware so the span includes wait time.
mcp.add_middleware(TelemetryMiddleware())
mcp.add_middleware(RateLimitMiddleware())

rr.register(mcp)


@mcp.tool
def ping() -> dict[str, str]:
    """Liveness probe — confirms the MCP server is up and tools are callable."""
    return {"status": "ok", "server": "coelhonexus-mcp"}


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "coelhonexus-mcp"})


# OpenTelemetryMiddleware extracts W3C traceparent so tool-call spans become children of the Celery span,
# not orphan root traces in LangFuse.
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
http_app = OpenTelemetryMiddleware(mcp.http_app())


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
    )
