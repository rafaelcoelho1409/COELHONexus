"""
FastAPI upstream client — async httpx wrapper + reverse-proxy helper.

Replaces the Go `net/http/httputil.ReverseProxy` used in apps/web/main.go's
`kdInspectProxyHandler`. Same contract: forward an incoming request (any
method, any body, any headers, any query string) to FastAPI under a remapped
path prefix and stream the response back to the client.

Why streaming: FastAPI's KD inspect router can return large rendered-markdown
HTML fragments and (in the future) SSE streams. Buffering would balloon
memory and break SSE entirely. We use Starlette's StreamingResponse so the
upstream body is forwarded chunk-by-chunk.
"""
import os
from typing import Optional

import httpx
from starlette.requests import Request
from starlette.responses import StreamingResponse


# Same env contract as apps/web (FASTAPI_URL points at in-cluster FastAPI svc).
# Local skaffold default targets the in-cluster Service DNS name; production
# Helm chart overrides via the configmap (see k8s/helm/templates/fasthtml/configmap.yaml).
FASTAPI_URL = os.environ.get("FASTAPI_URL", "http://coelhonexus-fastapi:8000").rstrip("/")

# Module-level shared client (one connection pool across the whole process).
# Created lazily on first use to play well with FastHTML/Starlette lifespan.
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Lazy-instantiate the httpx async client. Pool size 50 is plenty for
    our homelab traffic; per-request timeout is 30s for proxy + 10s for
    short health checks (callers can override via `timeout=` kwarg).

    `AsyncHTTPTransport(retries=3)` is critical: when the FastAPI pod
    restarts (Skaffold reload, OOM, image swap), its keepalive TCP
    connections die. Without this, the singleton pool reuses dead sockets
    and every request fails with `ConnectError: All connection attempts
    failed` until the pod is itself restarted. With retries, httpx
    re-resolves DNS + opens a fresh socket on transport errors."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=FASTAPI_URL,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            transport=httpx.AsyncHTTPTransport(retries=3),
        )
    return _client


# Headers that should NOT be forwarded — they're hop-by-hop or set by the
# httpx client itself. RFC 7230 §6.1 lists these as connection-specific.
_HOP_BY_HOP_REQ = frozenset({
    "host", "connection", "content-length", "transfer-encoding",
    "keep-alive", "te", "trailers", "upgrade", "proxy-authorization",
    "proxy-authenticate",
})
_HOP_BY_HOP_RESP = frozenset({
    "connection", "transfer-encoding", "keep-alive", "te", "trailers",
    "upgrade", "proxy-authenticate", "proxy-authorization",
})


async def reverse_proxy(request: Request, upstream_path: str) -> StreamingResponse:
    """
    Forward `request` to FastAPI at `upstream_path` (preserving method, headers,
    query string, body) and stream the response back. Drop-in replacement for
    Go's httputil.ReverseProxy.

    Args:
        request:        the incoming Starlette Request.
        upstream_path:  absolute path on FastAPI (must start with "/"),
                        e.g. "/api/v1/knowledge/inspect/frameworks".

    Returns:
        StreamingResponse mirroring the upstream's status, headers, and body.
    """
    client = _get_client()
    # Filter request headers (drop hop-by-hop)
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP_REQ
    }
    # Read incoming body — small payloads only (KD inspect has no large uploads).
    # If we ever proxy large file uploads, switch to .stream() here.
    body = await request.body()
    # Build the upstream request and send it in STREAM mode so the response
    # body stays unread until our generator pulls it. Without stream=True,
    # client.request() eagerly buffers the body; aiter_raw() then raises
    # StreamConsumed because the content has already been read into memory.
    req = client.build_request(
        method=request.method,
        url=upstream_path,
        params=dict(request.query_params),
        headers=fwd_headers,
        content=body,
    )
    upstream = await client.send(req, stream=True)
    # Filter response headers (drop hop-by-hop)
    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP_RESP
    }
    # Stream the response body back to the client; ensure the upstream
    # connection is closed even if the generator is interrupted (e.g. client
    # disconnect mid-stream).
    async def _gen():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
    return StreamingResponse(
        _gen(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


async def health_probe(timeout_s: float = 5.0) -> tuple[bool, str]:
    """
    Hit FastAPI /health and return (ok, body_text). Used by the home page's
    /api/test endpoint. Catches every exception so the caller can render a
    friendly error fragment.
    """
    try:
        client = _get_client()
        resp = await client.get("/health", timeout=timeout_s)
        if resp.status_code == 200:
            return True, resp.text
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"
