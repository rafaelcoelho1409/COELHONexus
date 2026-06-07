"""ycs/transcript — pure helpers (CDP URL resolver + transcript parser +
caption-track selection).

Direct port of deprecated `routers/v1/youtube/helpers.py:L780-892, L1148-1173`.
Pure side: no Playwright handle, no async I/O. The HTTP probe for CDP URL
resolution stays sync (`urllib.request.urlopen`) — `service.py` wraps it
in `asyncio.to_thread`."""
from __future__ import annotations

import json
import logging
import re
import ssl
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import urlopen


logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    timestamp: str
    text:      str


@dataclass
class CaptionTrack:
    language_code:     str
    name:              str
    is_auto_generated: bool
    base_url:          str


def _get_cdp_websocket_url(cdp_endpoint: str) -> str:
    """Resolve the WebSocket debugger URL from a CDP HTTP endpoint.

    Handles HTTPS reverse proxies (Tailscale Ingress) by reconstructing
    `wss://` from the same netloc rather than trusting the body's own
    `webSocketDebuggerUrl` (which points at the internal container).
    Falls back to the input if the probe fails."""
    parsed = urlparse(cdp_endpoint)
    json_url = f"{cdp_endpoint}/json/version"
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urlopen(json_url, timeout = 10, context = ctx) as response:
            data = json.loads(response.read().decode())
            ws_url = data.get("webSocketDebuggerUrl", "")
            if not ws_url:
                logger.warning(
                    f"[cdp] No webSocketDebuggerUrl in response from "
                    f"{json_url}",
                )
                return cdp_endpoint
            ws_parsed = urlparse(ws_url)
            ws_path = ws_parsed.path
            if parsed.scheme == "https":
                proper_url = f"wss://{parsed.netloc}{ws_path}"
            else:
                proper_url = f"ws://{parsed.netloc}{ws_path}"
            logger.info(f"[cdp] Resolved: {proper_url[:60]}...")
            return proper_url
    except Exception as e:
        logger.warning(f"[cdp] Failed to fetch {json_url}: {e}")
        return cdp_endpoint


def _close_stale_cdp_targets(cdp_endpoint: str) -> int:
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
    list_url = f"{cdp_endpoint}/json/list"
    closed = 0
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urlopen(list_url, timeout = 10, context = ctx) as response:
            targets = json.loads(response.read().decode())
    except Exception as e:
        logger.warning(f"[cdp] Failed to list targets at {list_url}: {e}")
        return 0
    stale_types = {
        "service_worker",
        "dedicated_worker",
        "shared_worker",
        "background_page",
    }
    for target in targets:
        ttype = target.get("type", "")
        tid = target.get("id", "")
        if ttype not in stale_types or not tid:
            continue
        close_url = f"{cdp_endpoint}/json/close/{tid}"
        try:
            with urlopen(close_url, timeout = 5, context = ctx) as r:
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


def _select_best_track(
    tracks:        list[CaptionTrack],
    prefer_manual: bool = True,
) -> CaptionTrack:
    """Pick the highest-priority track. English manual > Portuguese manual
    > any manual > English auto > any. Ports helpers.py:L880-891."""
    def priority(t: CaptionTrack) -> tuple[bool, int]:
        is_english    = t.language_code.startswith("en")
        is_portuguese = t.language_code.startswith("pt")
        return (
            t.is_auto_generated if prefer_manual else False,
            0 if is_english else (1 if is_portuguese else 2),
        )
    return sorted(tracks, key = priority)[0]


def _parse_transcript(raw_text: str) -> list[TranscriptSegment]:
    """Parse raw transcript text into timestamped segments.

    Strict port of helpers.py:L1148-1173. Handles the YouTube `Feb-Apr 2026`
    transcript-panel formats — timestamp line (`mm:ss`), optional duration
    line (`N seconds`), then text lines."""
    lines = [
        line.strip()
        for line in raw_text.split("\n")
        if line.strip()
    ]
    segments: list[TranscriptSegment] = []
    i = 0
    # Skip leading non-timestamp preamble
    while i < len(lines) and not re.match(r"^\d+:\d{2}$", lines[i]):
        i += 1
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\d+:\d{2}$", line):
            timestamp = line
            i += 1
            if i < len(lines) and re.match(r"^\d+\s+(second|minute)", lines[i]):
                i += 1
            text_parts: list[str] = []
            while i < len(lines) and not re.match(r"^\d+:\d{2}$", lines[i]):
                text_parts.append(lines[i])
                i += 1
            if text_parts:
                segments.append(
                    TranscriptSegment(
                        timestamp = timestamp,
                        text      = " ".join(text_parts),
                    ),
                )
        else:
            i += 1
    return segments


def classify_error(error_msg: str) -> str:
    """Return one of `permanent`, `retryable`, or `unknown` from a fetch-
    failure message. Mirrors helpers.py:L1666-1670 (`is_retryable`).

    Centralized here so batch retry logic and unit tests use the same
    rules."""
    from .params import PERMANENT_ERRORS, RETRYABLE_ERRORS
    error_lower = error_msg.lower()
    if any(p in error_lower for p in PERMANENT_ERRORS):
        return "permanent"
    if any(r in error_lower for r in RETRYABLE_ERRORS):
        return "retryable"
    return "unknown"
