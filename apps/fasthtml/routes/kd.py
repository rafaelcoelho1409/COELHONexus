"""
Knowledge Distiller routes — markdown inspector page + reverse-proxy.

Route map (parity with apps/web/main.go):
  GET  /kd/inspect             → KDInspectPage() shell (HTMX-driven 3-pane)
  *    /api/kd/inspect/<rest>  → reverse-proxy → FastAPI /api/v1/knowledge/inspect/<rest>

The proxy forwards EVERY method (GET / POST / PUT / DELETE) so the FastAPI
inspect router stays the only authoritative source of inspect-stage logic.
HTMX fragments emitted by FastAPI inline into the inspect page panes.
"""
from fasthtml.common import APIRouter
from starlette.requests import Request

from components.kd_inspect import KDInspectPage
from services.fastapi_client import reverse_proxy


ar = APIRouter()


@ar("/kd/inspect")
async def kd_inspect_page():
    """Render the 3-pane inspector shell. HTMX hydrates content from FastAPI."""
    return KDInspectPage()


# Reverse-proxy /api/kd/inspect/<rest> → /api/v1/knowledge/inspect/<rest>.
# `path:rest` captures the entire remainder (including slashes) so any
# FastAPI sub-route under /api/v1/knowledge/inspect/ is reachable through
# this single handler — exactly like Go's httputil.ReverseProxy in main.go.
@ar("/api/kd/inspect/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def kd_inspect_proxy(request: Request, rest: str):
    """Forward every method/body/query-string to FastAPI's inspect router."""
    upstream = f"/api/v1/knowledge/inspect/{rest}"
    return await reverse_proxy(request, upstream)
