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
import time
from typing import Optional
from urllib.request import urlopen

from playwright.async_api import async_playwright
from websockets.sync.client import connect as connect_websocket


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
    """Remove stale worker targets and unresponsive top-level blank pages.

    Why this exists: YouTube registers a service worker for every
    `www.youtube.com` page Playwright touches. Across runs, those
    workers persist on the long-lived sidecar Chromium. The next
    `connect_over_cdp` call enumerates ALL existing targets and asserts
    inside `_CRBrowser._onAttachedToTarget` (coreBundle.js:36978) when
    a target shape doesn't match — the Playwright driver process
    crashes and the Python client gets
    `BrowserType.connect_over_cdp: Connection closed while reading
    from the driver`. Empirically reproduced 2026-06-07.

    Some shared headed sessions also retain a broken top-level `about:blank`
    page. Its CDP socket opens, but `Page.enable` never replies; Playwright
    then blocks while auto-attaching to every page. Probe these blank pages
    and close only those that fail that command. Responsive blank tabs and
    loaded pages are left alone.

    Best-effort: HTTP / parse / websocket failures that do not prove a target
    is broken are logged and left alone. Returns the number of targets closed."""
    list_url = f"{cdp_http_url}/json/list"
    closed = 0
    ctx = _ssl_context()
    try:
        with urlopen(list_url, timeout = params.CDP_PROBE_TIMEOUT_S, context = ctx) as response:
            targets = json.loads(response.read().decode())
    except Exception as e:
        logger.warning(f"[cdp] Failed to list targets at {list_url}: {e}")
        return 0
    stale_targets = domain.stale_targets(targets)
    for target in domain.top_level_blank_page_targets(targets):
        responsive = _blank_page_target_is_responsive(cdp_http_url, target)
        if responsive is False:
            stale_targets.append(target)

    for target in stale_targets:
        ttype = target.get("type", "")
        tid = target.get("id", "")
        close_url = f"{cdp_http_url}/json/close/{tid}"
        try:
            with urlopen(close_url, timeout = params.CDP_CLOSE_TIMEOUT_S, context = ctx) as r:
                _ = r.read()
            closed += 1
            description = (
                "unresponsive top-level about:blank page"
                if ttype == "page"
                else f"stale {ttype} target"
            )
            logger.info(f"[cdp] Closed {description} {tid[:8]}…")
        except Exception as e:
            logger.warning(
                f"[cdp] Failed to close {ttype} target {tid[:8]}…: {e}"
            )
    if closed:
        logger.info(f"[cdp] Closed {closed} stale target(s) before attach")
    return closed


def _blank_page_target_is_responsive(
    cdp_http_url: str,
    target: dict,
) -> bool | None:
    """Check an about:blank target without changing its document or URL.

    Returns False only when the target accepts a websocket but fails to answer
    `Page.enable`; connection/proxy failures are inconclusive and leave it open.
    """
    debugger_url = target.get("webSocketDebuggerUrl", "")
    if not debugger_url:
        return None

    ws_url = domain.ws_url_from(cdp_http_url, debugger_url)
    try:
        with connect_websocket(
            ws_url,
            open_timeout = params.CDP_BLANK_PAGE_PROBE_TIMEOUT_S,
            close_timeout = 1,
            max_size = 1024 * 1024,
        ) as websocket:
            websocket.send(json.dumps({"id": 1, "method": "Page.enable"}))
            deadline = time.monotonic() + params.CDP_BLANK_PAGE_PROBE_TIMEOUT_S
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                message = json.loads(websocket.recv(timeout = remaining))
                if message.get("id") != 1:
                    continue
                if "error" in message:
                    logger.warning(
                        f"[cdp] Blank page target {target.get('id', '')[:8]}… "
                        "rejected Page.enable",
                    )
                    return False
                return True
    except TimeoutError:
        logger.warning(
            f"[cdp] Blank page target {target.get('id', '')[:8]}… "
            "did not answer Page.enable",
        )
        return False
    except Exception as error:
        logger.debug(
            f"[cdp] Blank page target probe inconclusive "
            f"({type(error).__name__})",
        )
        return None


async def start_playwright():
    """Start the Playwright driver; the caller `await`s `.stop()` on it when done."""
    return await async_playwright().start()


# Public surface for the rest of the code.
__all__ = ["close_stale_targets", "resolve_ws_url", "start_playwright"]
