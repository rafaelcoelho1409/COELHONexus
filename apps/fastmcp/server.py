"""COELHO Nexus — FastMCP server. Registers all domain tools and exposes the Streamable-HTTP ASGI app.

Deliberately a SIMPLE MCP server: no OpenTelemetry, no LangFuse. Those
stay in apps/fastapi/ (which owns the observability stack); a future
FastMCP project can re-add per-tool spans following this repo's git
history. What remains here is rate limiting + credential injection.

Boot order matters (documented once here so future edits keep it):
  1. logging — first, so every later step can log.
  2. credential injection — before domain registration (tool `register()`
     functions read os.environ for rate-limit config; module imports
     themselves are env-free). Best-effort, never raises.
  3. middleware — RateLimitMiddleware (per-tool politeness intervals).
  4. domain registration — rr tools + resources + prompts.

`create_server()` is the factory (SOTA shape: importable without booting
twice, testable). Module-level `mcp` / `http_app` preserve the
`uvicorn server:http_app` entrypoint in entrypoint.sh.
"""
import domains, middleware

import logging
import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


logger = logging.getLogger(__name__)


def create_server():
    """Build + wire the root FastMCP server. Warn-and-continue: a down
    MinIO at boot degrades to env-only keys, never crashes the pod —
    readiness is decided by /health, not by subsystem reachability."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Must run before domain registration — each tool's `register()`
    # reads os.environ for rate-limit config. Graceful by contract
    # (env-only fallback).
    try:
        injected = domains.settings.credentials.service.inject_user_keys_into_env(
            ("SEMANTIC_SCHOLAR_API_KEY",),
        )
        if injected:
            logger.info(f"[boot] injected {injected} user key(s) into env")
    except Exception as e:
        logger.warning(f"[boot] credential injection failed: {e} — env-only mode")

    mcp = FastMCP("coelhonexus-mcp")

    mcp.add_middleware(middleware.ratelimit.RateLimitMiddleware())

    domains.rr.server.register(mcp)

    @mcp.tool
    def ping() -> dict[str, str]:
        """Liveness probe — confirms the MCP server is up and tools are callable."""
        return {"status": "ok", "server": "coelhonexus-mcp"}

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "coelhonexus-mcp"})

    return mcp


mcp = create_server()
http_app = mcp.http_app()


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
    )
