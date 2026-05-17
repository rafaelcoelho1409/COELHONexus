"""Reverse proxy: /api/* → FastAPI.

Browsers can't reach the in-cluster FastAPI service directly. Without
this proxy, every `fetch('/api/...')` from inline JS would hit the
FastHTML port (3000) — which has no /api routes — and silently 404 with
HTML, breaking the whole wizard.

The route is registered by calling `register(rt)` from main.py so the
proxy is attached to the same FastHTML app instance that owns every
other route.
"""
import os
from typing import Optional

import httpx
from starlette.requests import Request
from starlette.responses import StreamingResponse


FASTAPI_URL = os.environ.get(
    "FASTAPI_URL", "http://coelhonexus-fastapi:8000"
).rstrip("/")


_HOP_BY_HOP_REQ = frozenset({
    "host", "connection", "content-length", "transfer-encoding",
    "keep-alive", "te", "trailers", "upgrade",
    "proxy-authorization", "proxy-authenticate",
})
_HOP_BY_HOP_RESP = frozenset({
    "connection", "transfer-encoding", "keep-alive", "te", "trailers",
    "upgrade", "proxy-authenticate", "proxy-authorization",
})

_proxy_client: Optional[httpx.AsyncClient] = None
_sse_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Lazy singleton — one connection pool reused across requests."""
    global _proxy_client
    if _proxy_client is None:
        _proxy_client = httpx.AsyncClient(
            base_url=FASTAPI_URL,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            transport=httpx.AsyncHTTPTransport(retries=3),
        )
    return _proxy_client


def _get_sse_client() -> httpx.AsyncClient:
    """Separate client for SSE / long-lived streaming requests. NO read
    timeout — the planner SSE channel stays open for the full run
    (5-30 min); a finite read timeout would close it mid-stream and
    surface as a 500."""
    global _sse_client
    if _sse_client is None:
        _sse_client = httpx.AsyncClient(
            base_url=FASTAPI_URL,
            timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        )
    return _sse_client


def _is_sse_request(request: Request) -> bool:
    """SSE detection — Accept header asks for text/event-stream, or the
    URL path ends in `/events` (our convention for SSE endpoints)."""
    accept = (request.headers.get("accept") or "").lower()
    if "text/event-stream" in accept:
        return True
    if request.url.path.rstrip("/").endswith("/events"):
        return True
    return False


async def _forward(request: Request) -> StreamingResponse:
    """Forward `request` to FastAPI at its same path. Preserves method,
    headers (minus hop-by-hop), body, and query string. Streams the
    response back so large payloads don't balloon memory."""
    upstream_path = request.url.path
    if request.url.query:
        upstream_path = f"{upstream_path}?{request.url.query}"

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP_REQ
    }
    body = await request.body()

    client = _get_sse_client() if _is_sse_request(request) else _get_client()
    upstream_req = client.build_request(
        method=request.method,
        url=upstream_path,
        headers=headers,
        content=body,
    )
    upstream_resp = await client.send(upstream_req, stream=True)

    response_headers = {
        k: v for k, v in upstream_resp.headers.items()
        if k.lower() not in _HOP_BY_HOP_RESP
    }

    async def _body_iter():
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        _body_iter(),
        status_code=upstream_resp.status_code,
        headers=response_headers,
        media_type=upstream_resp.headers.get("content-type"),
    )


def register(rt) -> None:
    """Attach the /api/{path:path} reverse-proxy route to `rt`."""
    @rt(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def api_proxy(req: Request, path: str):
        return await _forward(req)
