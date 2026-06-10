"""COELHO Nexus — FastMCP server entry point.

Assembly only: build the FastMCP server, register tools, and expose the
Streamable-HTTP ASGI app for uvicorn. This is the shared MCP tool surface —
the `fastmcp` peer app alongside `fastapi` (API) and `fasthtml` (BFF). The
Research Radar agent (apps/fastapi/domains/rr) connects to it as an MCP
*client*; the server itself stays ClusterIP/internal.

FastMCP's Streamable-HTTP transport is a Starlette ASGI app, so the launch
shape matches the other two apps:

  uvicorn server:http_app   →  MCP endpoint at /mcp/

BASELINE ONLY. Coming next (separate steps):
  - tools/      one subpackage per source (arxiv/, openalex/, …):
                tool.py (@mcp.tool, thin) · service.py (httpx I/O) ·
                domain.py (pure parse) · schemas.py · params.py
  - middleware/ on_call_tool: OTel span + LangFuse trace · per-source rate-limit
  - skaffold.yaml artifact + Helm Deployment/Service + portForward wiring
"""
import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP("coelhonexus-mcp")


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
http_app = mcp.http_app()


if __name__ == "__main__":
    # Convenience for local direct runs (`python server.py`); the container
    # path is uvicorn via entrypoint.sh.
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
    )
