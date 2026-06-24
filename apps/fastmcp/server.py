"""COELHO Nexus — FastMCP server entry point.

Assembly only: build the FastMCP server, register every domain's tools /
resources / prompts, and expose the Streamable-HTTP ASGI app for uvicorn.
This is the shared MCP tool surface — the `fastmcp` peer app alongside
`fastapi` (API) and `fasthtml` (BFF). The Research Radar agent
(apps/fastapi/domains/rr) connects to it as an MCP *client*; the server
itself stays ClusterIP/internal.

FastMCP's Streamable-HTTP transport is a Starlette ASGI app, so the launch
shape matches the other two apps:

  uvicorn server:http_app   →  MCP endpoint at /mcp/

Adding a new feature = (1) a new package under `domains/<feature>/` with its
own `register(mcp)` function, (2) one call to that register() below.
Mirrors the apps/fasthtml features.X.register(rt) convention.
"""
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

# Inject user-supplied tool API keys (Settings UI → MinIO+Fernet store) into
# os.environ BEFORE the tools import — they read `os.environ.get(...)` at
# module-import time to pick the rate-limit interval. Tuple of env-var names
# this peer app may consume; safe to extend as new tools land.
inject_user_keys_into_env(("SEMANTIC_SCHOLAR_API_KEY",))

# Bootstrap OTel SDK BEFORE any tool import resolves — sets up the dual
# exporter pipeline (Alloy + LangFuse) so the very first span emitted by
# TelemetryMiddleware below has a place to go. Idempotent + best-effort:
# missing endpoints become a no-op, missing creds skip that exporter.
init_otel()

mcp = FastMCP("coelhonexus-mcp")

# Cross-cutting middleware — runs on every tool call regardless of domain.
# Order matters: TelemetryMiddleware wraps RateLimitMiddleware so the span
# captures the WAIT time as part of the tool's apparent duration.
mcp.add_middleware(TelemetryMiddleware())
mcp.add_middleware(RateLimitMiddleware())

# Register every domain's MCP capabilities on the root server.
rr.register(mcp)


@mcp.tool
def ping() -> dict[str, str]:
    """Liveness probe — confirms the MCP server is up and tools are callable."""
    return {"status": "ok", "server": "coelhonexus-mcp"}


# Plain HTTP GET health endpoint for Kubernetes startup/liveness/readiness
# probes (the MCP /mcp/ endpoint needs a protocol handshake, so it can't be
# probed directly). Registered on the server BEFORE http_app is built.
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "coelhonexus-mcp"})


# Streamable-HTTP ASGI app for uvicorn. Mirrors apps/fastapi `app:app` and
# apps/fasthtml `main:app` — entrypoint.sh runs `uvicorn server:http_app`.
# Host/port are configured on uvicorn (entrypoint flags), not on the FastMCP
# instance, per FastMCP's deployment guidance.
#
# OpenTelemetryMiddleware extracts W3C traceparent headers from every
# incoming HTTP request so that MCP tool-call spans produced by
# TelemetryMiddleware become children of the Celery worker's rr.scan.run
# span rather than new root traces in LangFuse.
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
http_app = OpenTelemetryMiddleware(mcp.http_app())


if __name__ == "__main__":
    # Convenience for local direct runs (`python server.py`); the container
    # path is uvicorn via entrypoint.sh.
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
    )
