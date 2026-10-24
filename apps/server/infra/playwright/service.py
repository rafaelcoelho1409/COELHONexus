"""Playwright browser-service helpers: CDP endpoint resolution, stale-target cleanup, driver start.

The only module that imports `playwright` or reads the `PLAYWRIGHT_*` env vars (the Crawl4AI
library, which sits on top of these browsers, lives in the `crawl4ai/` submodule). Domains
reach the browsers dotted through here: `resolve_ws_url()` (HTTP CDP endpoint -> WebSocket
URL), `close_stale_targets()`, `start_playwright()` (the Playwright driver, on which the
caller calls `chromium.connect_over_cdp(ws_url)`), and the endpoints in `params`."""
from __future__ import annotations
from . import domain, params

import json
import logging
import ssl
from typing import Optional
from urllib.request import urlopen

from playwright.async_api import async_playwright


logger = logging.getLogger(__name__)


# HTTP CDP URL -> resolved WebSocket URL. Only filled for callers that ask for caching.
_ws_cache: dict[str, str] = {}


def _ssl_context() -> ssl.SSLContext:
    # Tailscale ingress sometimes serves internal certs.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def resolve_ws_url(cdp_http_url: str, *, cache: bool = False) -> Optional[str]:
    """HTTP CDP URL -> `ws(s)://…/devtools/browser/<id>` from `/json/version`.

    None on any failure so the caller can fall back (local Chromium, or the
    plain HTTP endpoint). `cache=True` remembers successes per worker process."""
    if not cdp_http_url:
        return None
    if cache and cdp_http_url in _ws_cache:
        return _ws_cache[cdp_http_url]
    json_url = f"{cdp_http_url.rstrip('/')}/json/version"
    try:
        with urlopen(json_url, timeout = params.CDP_PROBE_TIMEOUT_S, context = _ssl_context()) as response:
            data = json.loads(response.read().decode())
        debugger_url = data.get("webSocketDebuggerUrl", "")
        if not debugger_url:
            logger.warning(f"[cdp] no webSocketDebuggerUrl at {json_url}")
            return None
        resolved = domain.ws_url_from(cdp_http_url, debugger_url)
        logger.info(f"[cdp] Resolved: {resolved[:60]}...")
        if cache:
            _ws_cache[cdp_http_url] = resolved
        return resolved
    except Exception as e:
        logger.warning(f"[cdp] resolve failed for {cdp_http_url}: {e}")
        return None


def close_stale_targets(cdp_http_url: str) -> int:
    """Close any non-page targets (service_worker, dedicated_worker,
    shared_worker, background_page) lingering on the Chromium CDP
    sidecar before `connect_over_cdp` runs.

    Why this exists: YouTube registers a service worker for every
    `www.youtube.com` page Playwright touches. Across runs, those
    workers persist on the long-lived sidecar Chromium. The next
    `connect_over_cdp` call enumerates ALL existing targets and asserts
    inside `_CRBrowser._onAttachedToTarget` (coreBundle.js:36978) when
    a target shape doesn't match — the Playwright driver process
    crashes and the Python client gets
    `BrowserType.connect_over_cdp: Connection closed while reading
    from the driver`. Empirically reproduced 2026-06-07.

    Idempotent + best-effort: any HTTP / parse error logs a warning and
    returns 0, letting `connect_over_cdp` proceed (which may itself
    succeed if the failing target was already closed). Returns the
    number of targets actually closed."""
    list_url = f"{cdp_http_url}/json/list"
    closed = 0
    ctx = _ssl_context()
    try:
        with urlopen(list_url, timeout = params.CDP_PROBE_TIMEOUT_S, context = ctx) as response:
            targets = json.loads(response.read().decode())
    except Exception as e:
        logger.warning(f"[cdp] Failed to list targets at {list_url}: {e}")
        return 0
    for target in domain.stale_targets(targets):
        ttype = target.get("type", "")
        tid = target.get("id", "")
        close_url = f"{cdp_http_url}/json/close/{tid}"
        try:
            with urlopen(close_url, timeout = params.CDP_CLOSE_TIMEOUT_S, context = ctx) as r:
                _ = r.read()
            closed += 1
            logger.info(
                f"[cdp] Closed stale {ttype} target {tid[:8]}… "
                f"({target.get('title', '')[:60]})"
            )
        except Exception as e:
            logger.warning(
                f"[cdp] Failed to close {ttype} target {tid[:8]}…: {e}"
            )
    if closed:
        logger.info(f"[cdp] Closed {closed} stale target(s) before attach")
    return closed


async def start_playwright():
    """Start the Playwright driver; the caller `await`s `.stop()` on it when done."""
    return await async_playwright().start()


# Public surface for the rest of the code.
__all__ = ["close_stale_targets", "resolve_ws_url", "start_playwright"]
