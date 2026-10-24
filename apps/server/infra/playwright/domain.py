"""playwright domain — pure CDP URL / target helpers (no I/O, deterministic)."""
from __future__ import annotations
from . import params

from urllib.parse import urlparse


def ws_url_from(cdp_http_url: str, debugger_url: str) -> str:
    """`ws(s)://<netloc of cdp_http_url><path of debugger_url>`.

    Rebuilds the WebSocket URL from the HTTP endpoint's own netloc instead of
    trusting the body's `webSocketDebuggerUrl` (which points at the internal
    container). Handles HTTPS reverse proxies (Tailscale Ingress): `https` -> `wss`."""
    scheme = "wss" if urlparse(cdp_http_url).scheme == "https" else "ws"
    return f"{scheme}://{urlparse(cdp_http_url).netloc}{urlparse(debugger_url).path}"


def stale_targets(targets: list[dict]) -> list[dict]:
    """Targets from CDP `/json/list` that are non-page leftovers with an id."""
    return [
        t for t in targets
        if t.get("type", "") in params.STALE_TARGET_TYPES and t.get("id", "")
    ]
