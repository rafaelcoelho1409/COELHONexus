"""ycs/transcript — pure helpers (CDP URL resolver + transcript parser +
caption-track selection + innertube payload builders/parsers).
py` module docstring for the 4-path cascade.

Pure side: no Playwright handle, no async I/O. The HTTP probe for CDP URL
resolution stays sync (`urllib.request.urlopen`) — `service.py` wraps it
in `asyncio.to_thread`."""
from __future__ import annotations

import base64
import json
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Any
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


def build_get_panel_params(video_id: str) -> str:
    """Protobuf `params` for `/youtubei/v1/get_panel` with
    `panelId="PAmodern_transcript_view"` — the endpoint YouTube's
    modern transcript panel (Jun 2026 `PAmodern_transcript_view`
    experiment) loads its segments from.

    Layout (captured 2026-06-10 from the panel's own gzip POST body on
    a live watch page; empirically replayed 200 across videos):

        field 149 (length-delimited) {
            field 1 (string): video_id
            field 3 (varint): 1
        }

    `\\xaa\\x09` is the varint tag for (field=149, wire=2)."""
    inner = (
        b"\x0a" + bytes([len(video_id)]) + video_id.encode("ascii")
        + b"\x18\x01"
    )
    return base64.b64encode(
        b"\xaa\x09" + bytes([len(inner)]) + inner,
    ).decode("ascii")


def parse_get_panel_segments(data: Any) -> list[dict]:
    """Extract `[{timestamp, text}, ...]` from a `get_panel` response.

    The modern panel nests segments as
    `...macroMarkersPanelItemViewModel.item.timelineItemViewModel
    .contentItems[].transcriptSegmentViewModel{timestamp, simpleText}`.
    A recursive walk keeps this robust to the wrapper layers shifting
    (they already differ between videos with/without chapters). JSON
    document order == transcript order."""
    segments: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            seg = node.get("transcriptSegmentViewModel")
            if isinstance(seg, dict):
                text = (seg.get("simpleText") or "").strip()
                if text:
                    segments.append({
                        "timestamp": seg.get("timestamp") or "",
                        "text":      text,
                    })
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return segments


def parse_get_transcript_segments(data: Any) -> list[dict]:
    """Extract `[{timestamp, text}, ...]` from a legacy
    `/youtubei/v1/get_transcript` response
    (`transcriptSegmentRenderer{startMs, snippet.runs[].text}`).

    Kept for sessions NOT bucketed into the modern-panel experiment,
    where `get_transcript` still answers 200 (under the experiment it
    is server-disabled with HTTP 400 'Precondition check failed')."""
    segments: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            seg = node.get("transcriptSegmentRenderer")
            if isinstance(seg, dict):
                snippet = seg.get("snippet") or {}
                runs = snippet.get("runs") or []
                text = "".join(r.get("text", "") for r in runs).strip()
                if not text:
                    text = (snippet.get("simpleText") or "").strip()
                if text:
                    try:
                        start_ms = int(seg.get("startMs") or 0)
                    except (TypeError, ValueError):
                        start_ms = 0
                    minutes = start_ms // 60000
                    seconds = (start_ms // 1000) % 60
                    segments.append({
                        "timestamp": f"{minutes}:{seconds:02d}",
                        "text":      text,
                    })
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
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
