"""HTTP transport middleware for mounted FastMCP applications."""
from __future__ import annotations

import ipaddress
from starlette.responses import PlainTextResponse


class LocalDemoLoopbackMiddleware:
    """Limit the unauthenticated demo transport to loopback clients."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            client = scope.get("client")
            host = client[0] if client else ""
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = False
            if not is_loopback:
                response = PlainTextResponse(
                    "The local demo MCP endpoint accepts loopback clients only",
                    status_code = 403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def protect_local_demo_http_app(app, *, enabled: bool) -> None:
    """Install loopback enforcement when the MCP entrypoint enables the demo."""
    if enabled:
        app.add_middleware(LocalDemoLoopbackMiddleware)
