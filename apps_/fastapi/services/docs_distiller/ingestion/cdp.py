"""Resolve the remote Playwright CDP HTTP endpoint to its WebSocket URL.

The COELHO Cloud Playwright service exposes an HTTP CDP server (typically
`http://playwright.playwright.svc.cluster.local:9224`). Crawl4AI's
BrowserConfig needs the actual `wss://…/devtools/browser/<id>` URL, which
the CDP server publishes at `GET /json/version` under `webSocketDebuggerUrl`.

We probe once, rewrite scheme + host so the returned URL keeps using the
same ingress we reached HTTP on, then cache the result for the remainder
of the worker's lifetime (the browser-pool service is restarted rarely
enough that re-probing per crawl is unnecessary).
"""
import json
import logging
import ssl
from typing import Optional
from urllib.parse import urlparse
from urllib.request import urlopen


logger = logging.getLogger(__name__)

_cached: dict[str, str] = {}


def resolve_cdp_ws_url(cdp_http_url: str) -> Optional[str]:
    """HTTP CDP URL → wss://…/devtools/browser/<id>. Returns None on any
    failure so the caller can fall back to local Chromium (or skip the
    Playwright path entirely)."""
    if not cdp_http_url:
        return None
    if cdp_http_url in _cached:
        return _cached[cdp_http_url]
    parsed = urlparse(cdp_http_url)
    json_url = f"{cdp_http_url.rstrip('/')}/json/version"
    try:
        ctx = ssl.create_default_context()
        # COELHO Cloud's Tailscale ingress sometimes uses internal certs.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urlopen(json_url, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        ws_url = data.get("webSocketDebuggerUrl", "")
        if not ws_url:
            logger.warning(f"[cdp] no webSocketDebuggerUrl at {json_url}")
            return None
        ws_path = urlparse(ws_url).path
        scheme = "wss" if parsed.scheme == "https" else "ws"
        resolved = f"{scheme}://{parsed.netloc}{ws_path}"
        _cached[cdp_http_url] = resolved
        return resolved
    except Exception as e:
        logger.warning(f"[cdp] resolve failed for {cdp_http_url}: {e}")
        return None
