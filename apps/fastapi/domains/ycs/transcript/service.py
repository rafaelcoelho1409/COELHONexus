"""ycs/transcript — Playwright CDP transcript-extraction service.

Direct port of deprecated `routers/v1/youtube/helpers.py:L1179-1773`,
reworked 2026-06-10 after a live-cluster failure autopsy (3/5 missing
Capital Global videos reproduced; all HAD captions per yt-dlp metadata).

Extraction is now a 4-path cascade per video, behind an authoritative
availability gate:

  GATE   `ytInitialPlayerResponse` — unplayable status or 0 caption
         tracks → PERMANENT failure (`no_transcript`/unavailable), no
         retries, surfaced separately from infra failures in stats.
  PATH 1 `/youtubei/v1/get_panel` (panelId=PAmodern_transcript_view) —
         the data API behind YouTube's Jun-2026 modern transcript
         panel. In-page fetch, ~250 ms, no DOM interaction, immune to
         renderer experiments. Works even on unhydrated (blank) pages.
  PATH 2 `/youtubei/v1/get_transcript` (legacy) — for sessions NOT in
         the modern-panel experiment (where get_panel may not serve);
         under the experiment it answers 400 'Precondition check
         failed' and the cascade just moves on.
  PATH 3 DOM click-scrape (deprecated v4) — hydration-gated; the
         f5bff8e clear-all-timers massacre is GONE (it froze Polymer
         hydration on fast-DCL navigations → permanent blank page →
         'Transcript button not found' with bodyChars=0).
  PATH 4 timedtext `baseUrl&fmt=json3` + bgutil-PoT — last resort
         (YouTube answers empty 200 for most datacenter sessions even
         with a pot; kept because it costs one request).

Public:
  PlaywrightTranscriptService     — class with init / fetch / close
  get_transcript_service()        — accessor (lazy singleton)
  init_transcript_service(...)    — lifespan-style initializer
  close_transcript_service()      — lifespan-style cleanup
  fetch_transcriptions_batch(...) — high-level batch API (cache → fetch → index)

The PlaywrightTranscriptService class is kept TOGETHER per port-fidelity
(`feedback_port_fidelity`) — the deprecated kept it as one cohesive class
with browser-pool + semaphore + retry + health-check coupled. Refactoring
this class is explicitly out-of-scope for the port."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Callable

import httpx
from elasticsearch import AsyncElasticsearch
from playwright.async_api import async_playwright

from domains.ycs.es_index import index_transcriptions_to_elasticsearch
from infra.elasticsearch import INDEX_TRANSCRIPTIONS

from .domain import (
    _close_stale_cdp_targets,
    CaptionTrack,
    _get_cdp_websocket_url,
    _parse_transcript,
    _select_best_track,
    build_get_panel_params,
    parse_get_panel_segments,
    parse_get_transcript_segments,
)
from .params import (
    BLOCK_PATTERNS,
    BLOCK_RESOURCE_TYPES,
    BROWSER_REFRESH_INTERVAL,
    CDP_HEADED,
    CONNECT_TIMEOUT_S,
    CONTEXT_POOL_SIZE,
    DEFAULT_CHUNK_SIZE,
    INITIAL_RETRY_WAIT_S,
    MAX_CONCURRENT,
    MAX_RETRIES,
    NAVIGATION_TIMEOUT_MS,
    PERMANENT_ERRORS,
    POT_CACHE_SLACK_S,
    POT_PROVIDER_URL,
    POT_REQUEST_TIMEOUT_S,
    RETRY_LIMIT,
    RETRYABLE_ERRORS,
    TIMEOUT_MS,
)


log = logging.getLogger("uvicorn.error")


# =============================================================================
# Page-level helpers (helpers.py:L824-1145)
# =============================================================================
async def _setup_routes(page) -> None:
    """Set up aggressive resource blocking."""
    for pattern in BLOCK_PATTERNS:
        await page.route(pattern, lambda r: r.abort())

    async def block_by_type(route):
        if route.request.resource_type in BLOCK_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    await page.route("**/*", block_by_type)


async def _kill_youtube_background(page) -> None:
    """DOM lightening before the click-scrape path — pauses the video and
    removes the heavy sidebar / comments / recommendation trees so the
    transcript panel wins the render-frame race.

    2026-06-10 REWORK — the f5bff8e clear-all-timers massacre is GONE.
    Clearing every `setTimeout`/`setInterval` id right after
    `domcontentloaded` also killed Polymer's hydration scheduler on
    fast-DCL navigations: the watch page froze at `bodyChars=0` (no
    expand button, no transcript button, no panels — empirically 3/5
    Capital Global videos, with hydration confirmed healthy at ~15 s
    once the massacre was skipped). The DOM removals below only touch
    trees that exist AFTER hydration, so this is now called from the
    DOM path (which hydration-gates first), not right after goto."""
    await page.evaluate("""
        () => {
            const stats = { video: 0, secondary: 0, comments: 0, renderers: 0 };

            // Stop the video; keep the player node (removing it can
            // wedge the engagement-panel layout reflow mid-hydration).
            const video = document.querySelector("video");
            if (video) {
                video.pause();
                video.removeAttribute("src");
                try { video.load(); } catch (_) {}
                stats.video = 1;
            }

            // Remove the right-rail sidebar (recommendations)
            const secondary = document.querySelector("#secondary");
            if (secondary) { secondary.remove(); stats.secondary = 1; }

            // Remove the comments tree
            const comments = document.querySelector("#comments");
            if (comments) { comments.remove(); stats.comments = 1; }

            // Remove all video-card renderers (recommendation grids)
            document.querySelectorAll(
                "ytd-compact-video-renderer, ytd-video-renderer, ytd-grid-video-renderer"
            ).forEach((el) => { el.remove(); stats.renderers++; });

            return stats;
        }
    """)


async def _get_caption_tracks(page) -> list[CaptionTrack]:
    """Extract caption tracks from `ytInitialPlayerResponse`."""
    tracks_data = await page.evaluate('''
        () => {
            const caps = window.ytInitialPlayerResponse?.captions;
            if (!caps?.playerCaptionsTracklistRenderer?.captionTracks) return [];
            return caps.playerCaptionsTracklistRenderer.captionTracks.map(t => ({
                languageCode: t.languageCode || '',
                name: t.name?.simpleText || t.languageCode || '',
                isAutoGenerated: t.kind === 'asr' || (t.vssId || '').startsWith('a.'),
                baseUrl: t.baseUrl || ''
            }));
        }
    ''')
    return [
        CaptionTrack(
            language_code     = t["languageCode"],
            name              = t["name"],
            is_auto_generated = t["isAutoGenerated"],
            base_url          = t["baseUrl"],
        )
        for t in tracks_data
    ]


async def _get_player_state(page) -> dict[str, Any]:
    """Authoritative availability gate from `ytInitialPlayerResponse`
    (present in the initial HTML — readable even on unhydrated pages).

    Returns `{hasPlayerResponse, playability, playabilityReason,
    nTracks}`. `nTracks == 0` with `playability == "OK"` means the
    video genuinely has no captions — the ONLY case the batch should
    report as "no transcript available" (cross-checked against yt-dlp
    `automatic_captions`/`subtitles` metadata, which agreed on all
    sampled corpus videos)."""
    try:
        await page.wait_for_function(
            "() => !!window.ytInitialPlayerResponse",
            timeout = 8000,
        )
    except Exception:
        pass
    return await page.evaluate("""() => {
        const pr = window.ytInitialPlayerResponse || null;
        const tracks = pr?.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
        return {
            hasPlayerResponse: !!pr,
            playability:       pr?.playabilityStatus?.status || null,
            playabilityReason: pr?.playabilityStatus?.reason || null,
            nTracks:           tracks.length,
        };
    }""")


# Playability statuses that can never yield a transcript for this
# (anonymous, datacenter) session — permanent, no retry.
_UNPLAYABLE_STATUSES = frozenset({
    "ERROR",                 # deleted / bad id
    "UNPLAYABLE",            # region-block / embed-only / members-only
    "LOGIN_REQUIRED",        # private / age-gated sign-in wall
    "AGE_CHECK_REQUIRED",
    "CONTENT_CHECK_REQUIRED",
    "LIVE_STREAM_OFFLINE",
})


async def _fetch_via_get_panel(page, video_id: str) -> list[dict]:
    """PATH 1 — in-page POST to `/youtubei/v1/get_panel` for the modern
    transcript panel's data (`PAmodern_transcript_view`).

    This is the same request the panel's own click handler issues
    (captured + replayed 2026-06-10), so it carries the session's
    INNERTUBE_CONTEXT, cookies and origin. ~250 ms; no DOM interaction;
    works on blank/unhydrated pages. Raises ValueError on HTTP error,
    error payload, or empty segment list."""
    params = build_get_panel_params(video_id)
    result = await page.evaluate(
        """async (params) => {
            try {
                const g = (k) => (window.ytcfg && window.ytcfg.get)
                    ? window.ytcfg.get(k) : null;
                const ctx = g('INNERTUBE_CONTEXT');
                if (!ctx) return { __err: 'no INNERTUBE_CONTEXT' };
                const resp = await fetch('/youtubei/v1/get_panel?prettyPrint=false', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        context: ctx,
                        panelId: 'PAmodern_transcript_view',
                        params:  params,
                    }),
                });
                if (!resp.ok) return { __err: 'HTTP ' + resp.status };
                return await resp.json();
            } catch (e) {
                return { __err: String((e && e.message) || e) };
            }
        }""",
        params,
    )
    if isinstance(result, dict) and result.get("__err"):
        raise ValueError(f"get_panel: {result['__err']}")
    segments = parse_get_panel_segments(result)
    if not segments:
        raise ValueError("get_panel: no segments in response")
    return segments


async def _fetch_via_get_transcript(page) -> list[dict]:
    """PATH 2 — in-page POST to legacy `/youtubei/v1/get_transcript`,
    with `params` deep-searched from `ytInitialData`'s
    `getTranscriptEndpoint` (the pre-experiment panel wiring).

    Sessions bucketed into the modern-panel experiment get HTTP 400
    'Precondition check failed' here — that's expected; the caller
    falls through. Raises ValueError on any failure."""
    result = await page.evaluate(
        """async () => {
            try {
                const g = (k) => (window.ytcfg && window.ytcfg.get)
                    ? window.ytcfg.get(k) : null;
                const ctx = g('INNERTUBE_CONTEXT');
                if (!ctx) return { __err: 'no INNERTUBE_CONTEXT' };
                const found = [];
                const seen = new Set();
                const walk = (o, depth) => {
                    if (!o || typeof o !== 'object' || depth > 40 || seen.has(o)) return;
                    seen.add(o);
                    if (o.getTranscriptEndpoint && o.getTranscriptEndpoint.params) {
                        found.push(o.getTranscriptEndpoint.params);
                    }
                    for (const k in o) walk(o[k], depth + 1);
                };
                walk(window.ytInitialData, 0);
                if (!found.length) return { __err: 'no getTranscriptEndpoint in ytInitialData' };
                const resp = await fetch('/youtubei/v1/get_transcript?prettyPrint=false', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ context: ctx, params: found[0] }),
                });
                if (!resp.ok) return { __err: 'HTTP ' + resp.status };
                return await resp.json();
            } catch (e) {
                return { __err: String((e && e.message) || e) };
            }
        }""",
    )
    if isinstance(result, dict) and result.get("__err"):
        raise ValueError(f"get_transcript: {result['__err']}")
    segments = parse_get_transcript_segments(result)
    if not segments:
        raise ValueError("get_transcript: no segments in response")
    return segments


# PoT token cache — one entry per visitorData (the WEB-client content
# binding for caption/timedtext requests). Tokens live ~6h; we re-mint
# POT_CACHE_SLACK_S early. Module-level on purpose: the Celery worker
# processes share one event loop per task, and the binding is stable
# across videos within a browser session.
_pot_cache: dict[str, tuple[str, float]] = {}


async def _get_caption_po_token(page) -> str | None:
    """Mint (or reuse) a `pot` token for the page's visitorData via the
    bgutil-PoT sidecar — the SAME provider yt-dlp uses for formats.

    YouTube's timedtext endpoint silently returns HTTP 200 with an
    empty body when the caption URL has no `pot` param (the
    'direct-API fallback failed: ValueError: empty response' log
    pattern). The in-page fetch inherits session cookies but cookies
    alone don't satisfy the proof-of-origin check on datacenter IPs.

    Best-effort: returns None on any failure (missing visitorData,
    sidecar down, timeout) — the caller then fetches without a pot,
    which is the pre-2026-06-10 behavior."""
    try:
        visitor_data = await page.evaluate(
            """() => {
                try {
                    return (window.ytcfg && window.ytcfg.get
                                && window.ytcfg.get('VISITOR_DATA'))
                        || (window.ytInitialPlayerResponse
                                && window.ytInitialPlayerResponse
                                    .responseContext
                                && window.ytInitialPlayerResponse
                                    .responseContext.visitorData)
                        || '';
                } catch (_) { return ''; }
            }"""
        )
        if not visitor_data:
            log.info("[transcript-service] no visitorData on page; pot skipped")
            return None
        cached = _pot_cache.get(visitor_data)
        if cached and cached[1] - POT_CACHE_SLACK_S > time.time():
            return cached[0]
        async with httpx.AsyncClient(timeout = POT_REQUEST_TIMEOUT_S) as client:
            resp = await client.post(
                f"{POT_PROVIDER_URL}/get_pot",
                json = {"content_binding": visitor_data},
            )
            resp.raise_for_status()
            payload = resp.json()
        token = payload.get("poToken") or ""
        if not token:
            log.info("[transcript-service] pot sidecar returned no poToken")
            return None
        expires_at = payload.get("expiresAt") or ""
        try:
            expires_ts = datetime.fromisoformat(
                expires_at.replace("Z", "+00:00"),
            ).timestamp()
        except (TypeError, ValueError):
            expires_ts = time.time() + 1800.0  # conservative 30 min
        _pot_cache[visitor_data] = (token, expires_ts)
        return token
    except Exception as e:
        log.info(
            f"[transcript-service] pot mint failed "
            f"({type(e).__name__}: {str(e)[:80]}); fetching without pot"
        )
        return None


async def _fetch_transcript_direct(
    page, base_url: str, po_token: str | None = None,
) -> list[dict]:
    """In-page direct fetch of YouTube's caption URL — fallback for
    videos where the DOM hides Show-transcript controls (age-restricted
    / sensitive-topic / unauthenticated-session UI variants).

    Calls `{base_url}&fmt=json3[&pot=…&c=WEB]` from inside the page
    context so the request inherits the session's cookies. Returns
    `[{timestamp, text}, ...]` segments.

    2026-06-10: `po_token` added. The original port assumed the session
    cookies carried the bgutil-PoT — they don't (the sidecar feeds
    yt-dlp's requests, not in-page fetches), which is why every
    fallback died with `empty response` (timedtext's silent rejection
    for pot-less callers). The token is appended only when the baseUrl
    doesn't already carry one.

    Raises `ValueError(reason)` on HTTP / JSON / empty-response."""
    json_url = base_url + ("&" if "?" in base_url else "?") + "fmt=json3"
    pot_applied = False
    if po_token and "pot=" not in base_url:
        json_url += "&pot=" + po_token
        if "?c=" not in base_url and "&c=" not in base_url:
            json_url += "&c=WEB"
        pot_applied = True
    result = await page.evaluate(f"""
        async () => {{
            try {{
                const resp = await fetch("{json_url}", {{
                    credentials: "include",
                    headers: {{ "Accept": "application/json" }}
                }});
                if (!resp.ok) {{
                    return {{ error: "HTTP " + resp.status }};
                }}
                const text = await resp.text();
                if (!text || text.length === 0) {{
                    return {{ error: "empty response" }};
                }}
                if (text.startsWith('<')) {{
                    return {{ error: "blocked (HTML response)" }};
                }}
                if (text.length < 10) {{
                    return {{ error: "truncated response: " + text.length + " bytes" }};
                }}
                try {{
                    return JSON.parse(text);
                }} catch (parseErr) {{
                    return {{ error: "JSON parse failed: " + parseErr.message + " (len=" + text.length + ")" }};
                }}
            }} catch (e) {{
                return {{ error: e.message }};
            }}
        }}
    """)
    if isinstance(result, dict) and "error" in result:
        # Tag pot presence in the error so the log fingerprint tells
        # "pot didn't help" apart from "no pot was available".
        suffix = " (with pot)" if pot_applied else " (no pot)"
        raise ValueError(str(result["error"]) + suffix)
    segments: list[dict] = []
    for event in (result or {}).get("events", []) or []:
        if "segs" in event:
            text = "".join(s.get("utf8", "") for s in event["segs"]).strip()
            if text:
                start_ms = event.get("tStartMs", 0)
                minutes  = start_ms // 60000
                seconds  = (start_ms // 1000) % 60
                segments.append({
                    "timestamp": f"{minutes}:{seconds:02d}",
                    "text":      text,
                })
    return segments


async def _extract_via_dom(page, timeout_ms: int) -> str:
    """Extract transcript via DOM interaction (deprecated v4 with smart waits).

    Strategy:
      1. Wait for video player to be ready (indicates page loaded)
      2. Wait for and click expand button (or skip if not found)
      3. Wait for and click transcript button
      4. Poll for transcript panel to load (up to 15 attempts)
      5. Extract content"""
    try:
        await page.wait_for_selector(
            "#movie_player, ytd-player",
            state   = "attached",
            timeout = 15000,
        )
        await page.wait_for_selector(
            "ytd-watch-metadata, #above-the-fold",
            state   = "attached",
            timeout = 10000,
        )
        log.info("[dom] Page ready (player + metadata loaded)")
    except Exception:
        await page.wait_for_timeout(3000)
        log.info("[dom] Fallback wait completed")
    # Hydration gate — selectors above can be ATTACHED while Polymer is
    # still painting skeleton placeholders (observed: bodyChars 12 → 783
    # → 2612 over 15 s on CPU-starved renders; the expand / transcript
    # buttons only become clickable near the end of that ramp). Without
    # this gate the selector budget below burns out BEFORE the buttons
    # exist and the video is misreported as 'Transcript button not
    # found'.
    try:
        await page.wait_for_function(
            "() => (document.body?.innerText?.length || 0) > 500",
            timeout = 20000,
        )
    except Exception:
        log.warning("[dom] Hydration gate not reached in 20s, proceeding")
    # Check if transcript panel is already visible
    already_visible = await page.evaluate('''() => {
        const segments = document.querySelectorAll('transcript-segment-view-model, ytd-transcript-segment-renderer');
        if (segments.length > 0) return true;
        const panel = document.querySelector('ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]');
        return panel && /\\d+:\\d{2}/.test(panel.innerText);
    }''')
    if already_visible:
        log.info("[dom] Transcript panel already visible")
        return await _extract_transcript_text(page)
    # Step 2: Wait for and click expand button (with retry)
    expanded = False
    for expand_attempt in range(3):
        try:
            expand_btn = await page.wait_for_selector(
                "tp-yt-paper-button#expand:not([hidden])",
                state   = "visible",
                timeout = 5000,
            )
            if expand_btn:
                await expand_btn.scroll_into_view_if_needed()
                await expand_btn.click()
                log.info("[dom] Description expanded")
                await page.wait_for_selector(
                    "ytd-video-description-transcript-section-renderer",
                    state   = "attached",
                    timeout = 5000,
                )
                expanded = True
                break
        except Exception:
            if expand_attempt < 2:
                await page.wait_for_timeout(1000)
            continue
    if not expanded:
        log.info("[dom] Expand button not found after retries, continuing...")
    # Step 3: Find and click transcript button with multiple selectors
    transcript_clicked = False
    selectors = [
        '[aria-label="Show transcript"]',
        "ytd-video-description-transcript-section-renderer button",
        'button[aria-label*="transcript" i]',
    ]
    for selector in selectors:
        try:
            btn = await page.wait_for_selector(
                selector,
                state   = "visible",
                timeout = 3000,
            )
            if btn:
                await btn.scroll_into_view_if_needed()
                await btn.click()
                log.info(f"[dom] Transcript button clicked: {selector}")
                transcript_clicked = True
                break
        except Exception:
            continue
    if not transcript_clicked:
        debug_info = await page.evaluate('''() => ({
            hasExpandBtn: !!document.querySelector('tp-yt-paper-button#expand'),
            hasTranscriptSection: !!document.querySelector('ytd-video-description-transcript-section-renderer'),
            hasShowTranscriptBtn: !!document.querySelector('[aria-label="Show transcript"]'),
            descExpanded: document.querySelector('ytd-text-inline-expander')?.hasAttribute('is-expanded'),
            url: window.location.href,
        })''')
        log.warning(f"[dom] Transcript button not found. Debug: {debug_info}")
        raise ValueError("Transcript button not found")
    # Step 4: Event-driven wait for transcript panel to render.
    # 1:1 with the gold-standard `wait_for_segments(page, timeout_ms=10000)`
    # from commit f5bff8e (`scripts/optimized_transcript_extraction.py`).
    # Replaces the prior 15-attempt × 1s polling loop — the polling
    # version was BLIND to the actual segment-render event and ate the
    # full 14s of `wait_for_timeout` sleep even when segments arrived
    # in 200ms. `wait_for_function` returns the moment the predicate
    # becomes true, OR raises on timeout (10s budget — predicate fires
    # within <2s on healthy renders).
    try:
        await page.wait_for_function(
            """() => {
                const segments = document.querySelectorAll(
                    'transcript-segment-view-model, ytd-transcript-segment-renderer, .segment-text'
                );
                if (segments.length > 0) return true;
                const panel = document.querySelector(
                    'ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]'
                );
                if (panel && /\\d+:\\d{2}/.test(panel.innerText)) return true;
                const newPanel = document.querySelector(
                    'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"]'
                );
                return newPanel && /\\d+:\\d{2}/.test(newPanel.innerText);
            }""",
            timeout = 10000,
        )
        # Best-effort segment count for telemetry — fire-and-forget.
        try:
            segment_count = await page.evaluate(
                """() => document.querySelectorAll(
                    'transcript-segment-view-model, ytd-transcript-segment-renderer'
                ).length""",
            )
        except Exception:
            segment_count = 0
        log.info(f"[dom] Panel loaded segments={segment_count}")
    except Exception:
        log.warning("[dom] Panel not loaded within 10s budget")
        raise ValueError("Transcript panel not loaded")
    return await _extract_transcript_text(page)


async def _extract_transcript_text(page) -> str:
    """Extract text from visible transcript panel.

    Updated for YouTube Feb 2026 UI with multiple fallback strategies:
      1. New `transcript-segment-view-model` elements
      2. New `engagement-panel-searchable-transcript` panel
      3. Legacy `ytd-engagement-panel` with `visibility` attribute"""
    return await page.evaluate('''
        () => {
            // Method 1: Feb 2026 UI - transcript-segment-view-model with .segment-text
            const segmentTexts = document.querySelectorAll(
                'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"] .segment-text'
            );
            if (segmentTexts.length > 0) {
                const parts = [];
                segmentTexts.forEach(el => {
                    const container = el.closest('ytd-transcript-segment-renderer, transcript-segment-view-model');
                    const timestamp = container?.querySelector('.segment-timestamp')?.innerText?.trim() || '';
                    const text = el.innerText?.trim() || '';
                    if (timestamp && text) {
                        parts.push(timestamp + '\\n' + text);
                    } else if (text) {
                        parts.push(text);
                    }
                });
                if (parts.length > 0) return parts.join('\\n');
            }
            // Method 2: Modern transcript-segment-view-model (Apr 2026 UI)
            const segmentModels = document.querySelectorAll('transcript-segment-view-model');
            if (segmentModels.length > 0) {
                const parts = [];
                segmentModels.forEach(seg => {
                    const tsEl = seg.querySelector('[class*="Timestamp"], .ytwTranscriptSegmentTimestampContainer div');
                    const textEl = seg.querySelector('.yt-core-attributed-string, [class*="Text"]');
                    const timestamp = tsEl?.innerText?.trim() || '';
                    const text = textEl?.innerText?.trim() || '';
                    if (timestamp && text) {
                        parts.push(timestamp + '\\n' + text);
                    } else if (seg.innerText) {
                        parts.push(seg.innerText.trim());
                    }
                });
                if (parts.length > 0) return parts.join('\\n');
            }
            // Method 3: New panel by target-id
            const newPanel = document.querySelector(
                'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"]'
            );
            if (newPanel && /\\d+:\\d{2}/.test(newPanel.innerText)) {
                return newPanel.innerText;
            }
            // Method 4: Legacy - old visibility attribute
            const panels = document.querySelectorAll('ytd-engagement-panel-section-list-renderer');
            for (const p of panels) {
                if (p.getAttribute('visibility') === 'ENGAGEMENT_PANEL_VISIBILITY_EXPANDED'
                    && /\\d+:\\d{2}/.test(p.innerText)) {
                    return p.innerText;
                }
            }
            return '';
        }
    ''')


# =============================================================================
# PlaywrightTranscriptService — Browser Pool with Semaphore Control
# =============================================================================
class PlaywrightTranscriptService:
    """Browser pool with semaphore-controlled concurrency for transcript
    extraction. Direct port of deprecated helpers.py:L1179-1723.

    Features:
      - Semaphore limits concurrent browser contexts (default: 5)
      - Context pool for reuse (reduces context creation overhead)
      - Browser-refresh tracking (recreate after N videos to dodge stale CDP)
      - Health-checked refresh + retry-with-backoff on CDP reconnect
      - Memory-safe: proper cleanup in all paths

    Usage (Celery task wrapper):
        service = PlaywrightTranscriptService(max_concurrent=5)
        await service.initialize()
        try:
            results = await service.fetch_batch(video_ids)
        finally:
            await service.close()
    """

    def __init__(
        self,
        cdp_url:                  str | None = None,
        max_concurrent:           int        = MAX_CONCURRENT,
        context_pool_size:        int | None = None,
        timeout_ms:               int        = TIMEOUT_MS,
        navigation_timeout_ms:    int        = NAVIGATION_TIMEOUT_MS,
        browser_refresh_interval: int        = BROWSER_REFRESH_INTERVAL,
        max_retries:              int        = MAX_RETRIES,
    ) -> None:
        """Args mirror deprecated `__init__` (helpers.py:L1201-L1242)."""
        self._cdp_endpoint = cdp_url
        self.max_concurrent = max_concurrent
        # Pool size should match max_concurrent to avoid context-creation storms.
        self.context_pool_size = (
            context_pool_size
            if context_pool_size is not None
            else max_concurrent
        )
        self.timeout_ms = timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms
        self.browser_refresh_interval = browser_refresh_interval
        self.max_retries = max_retries
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._playwright = None
        self._browser = None
        self._context_pool: asyncio.Queue = None
        self._initialized = False
        self._cdp_url: str | None = None
        # Browser refresh tracking
        self._videos_since_refresh = 0
        self._refresh_lock = asyncio.Lock()
        self._total_extractions = 0
        self._total_errors = 0
        # Active operations counter - prevents refresh during in-flight requests
        self._active_ops = 0
        self._active_ops_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize browser and context pool. Call once at startup."""
        if self._initialized:
            return
        # YouTube blocks headless for captions extraction → always HEADED
        cdp_endpoint = self._cdp_endpoint or CDP_HEADED
        # Sweep stale service-worker / dedicated-worker targets left
        # over from a previous run BEFORE attaching — otherwise the
        # Playwright driver crashes in `_onAttachedToTarget`. See
        # `domain._close_stale_cdp_targets` for the full story.
        await asyncio.to_thread(_close_stale_cdp_targets, cdp_endpoint)
        self._cdp_url = await asyncio.to_thread(
            _get_cdp_websocket_url, cdp_endpoint,
        )
        log.info(
            f"[transcript-service] Initializing with CDP: {self._cdp_url[:60]}...",
        )
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            self._cdp_url,
        )
        self._context_pool = asyncio.Queue(maxsize = self.context_pool_size)
        for i in range(self.context_pool_size):
            ctx = await self._create_context()
            await self._context_pool.put(ctx)
            log.info(
                f"[transcript-service] Warmed context "
                f"{i + 1}/{self.context_pool_size}",
            )
        self._initialized = True
        log.info(
            f"[transcript-service] Ready "
            f"(max_concurrent={self.max_concurrent}, "
            f"pool_size={self.context_pool_size})",
        )

    async def close(self) -> None:
        """Cleanup all resources. Call at shutdown."""
        if not self._initialized:
            return
        log.info("[transcript-service] Shutting down...")
        closed = 0
        while not self._context_pool.empty():
            try:
                ctx = self._context_pool.get_nowait()
                await ctx.close()
                closed += 1
            except Exception:
                pass
        log.info(f"[transcript-service] Closed {closed} pooled contexts")
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._initialized = False
        log.info("[transcript-service] Shutdown complete")

    async def _refresh_browser(
        self,
        max_retries:  int   = RETRY_LIMIT,
        initial_wait: float = INITIAL_RETRY_WAIT_S,
    ) -> None:
        """Refresh browser connection to prevent stale CDP connections.

        Caller must hold `_refresh_lock` (asyncio locks are not reentrant).
        Connect_over_cdp can hang indefinitely (known Playwright bug) so
        each attempt is wrapped in `asyncio.wait_for(timeout=CONNECT_TIMEOUT_S)`."""
        log.info(
            f"[transcript-service] Refreshing browser "
            f"(after {self._videos_since_refresh} videos)...",
        )
        # 1. Drain and close all pooled contexts
        closed = 0
        while not self._context_pool.empty():
            try:
                ctx = self._context_pool.get_nowait()
                await ctx.close()
                closed += 1
            except Exception:
                pass
        # 2. Close old browser
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                log.warning(
                    f"[transcript-service] Error closing old browser: {e}",
                )
        # 3. Re-resolve CDP URL and connect with retry
        cdp_endpoint = self._cdp_endpoint or CDP_HEADED
        connect_timeout = CONNECT_TIMEOUT_S
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                log.info(
                    f"[transcript-service] CDP reconnect attempt "
                    f"{attempt + 1}/{max_retries}...",
                )
                # Sweep stale workers between every reconnect attempt
                # — `refresh_interval=10` means this fires roughly every
                # 10 videos and is exactly where the assertion crash
                # would otherwise resurface.
                await asyncio.to_thread(
                    _close_stale_cdp_targets, cdp_endpoint,
                )
                self._cdp_url = await asyncio.wait_for(
                    asyncio.to_thread(_get_cdp_websocket_url, cdp_endpoint),
                    timeout = connect_timeout,
                )
                self._browser = await asyncio.wait_for(
                    self._playwright.chromium.connect_over_cdp(self._cdp_url),
                    timeout = connect_timeout,
                )
                log.info(
                    f"[transcript-service] CDP connected "
                    f"(attempt {attempt + 1})",
                )
                break
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"CDP connect timed out after {connect_timeout}s",
                )
                if attempt < max_retries - 1:
                    wait_time = initial_wait * (2 ** attempt)
                    log.warning(
                        f"[transcript-service] CDP connect TIMEOUT "
                        f"(attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {wait_time}s",
                    )
                    await asyncio.sleep(wait_time)
                else:
                    log.error(
                        f"[transcript-service] CDP connect timed out "
                        f"after {max_retries} attempts",
                    )
                    raise RuntimeError(
                        f"Failed to connect to Playwright CDP after "
                        f"{max_retries} attempts (timeout)",
                    ) from last_error
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = initial_wait * (2 ** attempt)
                    log.warning(
                        f"[transcript-service] CDP connect failed "
                        f"(attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {wait_time}s: {e}",
                    )
                    await asyncio.sleep(wait_time)
                else:
                    log.error(
                        f"[transcript-service] CDP connect failed after "
                        f"{max_retries} attempts: {e}",
                    )
                    raise RuntimeError(
                        f"Failed to connect to Playwright CDP after "
                        f"{max_retries} attempts",
                    ) from last_error
        # 4. Re-warm context pool
        self._context_pool = asyncio.Queue(maxsize = self.context_pool_size)
        for i in range(self.context_pool_size):
            ctx = await self._create_context()
            await self._context_pool.put(ctx)
        self._videos_since_refresh = 0
        log.info(
            f"[transcript-service] Browser refreshed "
            f"(closed {closed} contexts, warmed {self.context_pool_size} new)",
        )

    async def _cleanup_contexts(self) -> None:
        """Force close all pooled contexts and recreate fresh ones."""
        async with self._refresh_lock:
            closed = 0
            while not self._context_pool.empty():
                try:
                    ctx = self._context_pool.get_nowait()
                    await ctx.close()
                    closed += 1
                except Exception:
                    pass
            for i in range(self.context_pool_size):
                try:
                    ctx = await self._create_context()
                    await self._context_pool.put(ctx)
                except Exception as e:
                    log.warning(
                        f"[transcript-service] Failed to recreate "
                        f"context {i}: {e}",
                    )
            log.info(
                f"[transcript-service] Cleanup: closed {closed}, "
                f"recreated {self._context_pool.qsize()} contexts",
            )

    async def _check_browser_health(self) -> bool:
        """Return True if the browser connection is still healthy."""
        if not self._browser:
            return False
        try:
            if not self._browser.is_connected():
                return False
            return True
        except Exception as e:
            log.warning(
                f"[transcript-service] Browser health check failed: {e}",
            )
            return False

    async def _ensure_healthy_browser(self) -> None:
        """Ensure browser is healthy, refresh if needed."""
        if not await self._check_browser_health():
            async with self._refresh_lock:
                if await self._check_browser_health():
                    return
                # Wait for active ops to drain (max 30s)
                for _ in range(60):
                    async with self._active_ops_lock:
                        if self._active_ops == 0:
                            break
                    await asyncio.sleep(0.5)
                log.warning(
                    f"[transcript-service] Browser unhealthy, refreshing "
                    f"(active_ops={self._active_ops})",
                )
                await self._refresh_browser()

    async def _create_context(self):
        """Create a new browser context with optimized settings."""
        return await self._browser.new_context(
            viewport = {"width": 1920, "height": 1080},
        )

    async def _acquire_context(self, timeout: float = 30.0):
        """Get a context from pool, waiting if necessary."""
        try:
            return await asyncio.wait_for(
                self._context_pool.get(),
                timeout = timeout,
            )
        except asyncio.TimeoutError:
            log.warning(
                "[transcript-service] Context pool timeout, creating temporary",
            )
            return await self._create_context()

    async def _release_context(
        self,
        ctx,
        reuse:   bool  = True,
        timeout: float = 5.0,
    ) -> None:
        """Return context to pool or close it (timeout to prevent hangs)."""
        async def _close_ctx():
            try:
                await ctx.close()
            except Exception:
                pass
        if not reuse:
            try:
                await asyncio.wait_for(_close_ctx(), timeout = timeout)
            except asyncio.TimeoutError:
                log.warning(
                    "[transcript-service] Context close timed out",
                )
            return
        if self._context_pool.qsize() < self.context_pool_size:
            try:
                await asyncio.wait_for(
                    ctx.clear_cookies(), timeout = timeout,
                )
                self._context_pool.put_nowait(ctx)
            except asyncio.TimeoutError:
                log.warning(
                    "[transcript-service] Cookie clear timed out, "
                    "discarding context",
                )
            except Exception:
                try:
                    await asyncio.wait_for(
                        _close_ctx(), timeout = timeout,
                    )
                except Exception:
                    pass
        else:
            try:
                await asyncio.wait_for(_close_ctx(), timeout = timeout)
            except Exception:
                pass

    async def fetch_single(
        self,
        video_id:      str,
        prefer_manual: bool = True,
    ) -> dict[str, Any]:
        """Fetch transcript for a single video with semaphore + retry."""
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await self._fetch_single_attempt(
                    video_id, prefer_manual, attempt,
                )
                if "error" not in result:
                    return result
                error_msg = result.get("error", "").lower()
                if any(
                    x in error_msg
                    for x in ("no transcript", "button not found", "unavailable")
                ):
                    return result
                last_error = result.get("error")
            except Exception as e:
                last_error = str(e)
            if attempt < self.max_retries:
                wait_time = 2 ** attempt
                log.info(
                    f"[transcript-service] {video_id} retry "
                    f"{attempt + 1}/{self.max_retries} in {wait_time}s",
                )
                await asyncio.sleep(wait_time)
        return {
            "video_id": video_id,
            "error":    last_error or "Max retries exceeded",
        }

    async def _fetch_single_attempt(
        self,
        video_id:      str,
        prefer_manual: bool,
        attempt:       int = 0,
    ) -> dict[str, Any]:
        """Single extraction attempt (called by fetch_batch with batch retry)."""
        start_time = time.time()
        if attempt == 0:
            # Small staggered delay to reduce CDP pressure (0-500ms)
            await asyncio.sleep(0.1 * (hash(video_id) % 5))
        async with self.semaphore:
            await self._ensure_healthy_browser()
            async with self._active_ops_lock:
                self._active_ops += 1
            context = await self._acquire_context()
            page = None
            reuse_context = True
            try:
                self._videos_since_refresh += 1
                self._total_extractions += 1
                page = await context.new_page()
                await _setup_routes(page)
                url = f"https://www.youtube.com/watch?v={video_id}"
                # `wait_until="domcontentloaded"` per the gold-standard
                # script — was `"load"`, which waited on the very
                # analytics/manifest requests we abort via BLOCK_PATTERNS,
                # eating navigation budget for nothing. DCL fires as
                # soon as the HTML is parsed; the availability gate +
                # data-path fetches below read window globals that are
                # already in the initial HTML.
                await page.goto(
                    url,
                    wait_until = "domcontentloaded",
                    timeout    = self.navigation_timeout_ms,
                )
                # ---- Availability gate (permanent classifications) ----
                state = await _get_player_state(page)
                playability = state.get("playability")
                if playability and playability in _UNPLAYABLE_STATUSES:
                    reason = state.get("playabilityReason") or ""
                    log.info(
                        f"[transcript-service] {video_id} unplayable: "
                        f"{playability} {reason[:80]}",
                    )
                    return {
                        "video_id":      video_id,
                        "error":         (
                            f"video unavailable: {playability}"
                            + (f" ({reason})" if reason else "")
                        ),
                        "no_transcript": True,
                    }
                tracks = await _get_caption_tracks(page)
                if state.get("hasPlayerResponse") and not tracks:
                    # Authoritative: playable video, zero caption tracks
                    # → there IS no transcript. Permanent; never retried;
                    # batch stats bucket this separately from infra
                    # failures.
                    log.info(
                        f"[transcript-service] {video_id}: no caption "
                        f"tracks (playability={playability})",
                    )
                    return {
                        "video_id":      video_id,
                        "error":         "no transcript available (video has no caption tracks)",
                        "no_transcript": True,
                    }
                language = "auto"
                is_auto_generated = True
                selected = None
                if tracks:
                    manual_count = sum(
                        1 for t in tracks if not t.is_auto_generated
                    )
                    log.info(
                        f"[transcript-service] {video_id}: "
                        f"tracks={len(tracks)} manual={manual_count}",
                    )
                    selected = _select_best_track(tracks, prefer_manual)
                    language = selected.language_code
                    is_auto_generated = selected.is_auto_generated

                def _ok(segments: list[dict], method: str, note: str = "") -> dict[str, Any]:
                    elapsed = time.time() - start_time
                    log.info(
                        f"[transcript-service] OK {video_id} method={method} "
                        f"segments={len(segments)} time={elapsed:.2f}s{note}",
                    )
                    return {
                        "video_id":          video_id,
                        "language":          language,
                        "is_auto_generated": is_auto_generated,
                        "page_content":      " ".join(s["text"] for s in segments),
                        "segments":          segments,
                        "method":            method,
                    }

                # ---- PATH 1: modern get_panel data API ----
                try:
                    return _ok(
                        await _fetch_via_get_panel(page, video_id),
                        "get_panel",
                    )
                except Exception as e:
                    log.info(
                        f"[transcript-service] {video_id} get_panel path: "
                        f"{str(e)[:100]}",
                    )
                # ---- PATH 2: legacy get_transcript data API ----
                try:
                    return _ok(
                        await _fetch_via_get_transcript(page),
                        "get_transcript",
                    )
                except Exception as e:
                    log.info(
                        f"[transcript-service] {video_id} get_transcript path: "
                        f"{str(e)[:100]}",
                    )
                # ---- PATH 3: DOM click-scrape ----
                try:
                    await _kill_youtube_background(page)
                    raw_text = await _extract_via_dom(page, self.timeout_ms)
                    if not raw_text:
                        raise ValueError(f"No transcript for: {video_id}")
                    parsed = _parse_transcript(raw_text)
                    if "auto-generated" in raw_text.lower():
                        is_auto_generated = True
                    return _ok(
                        [
                            {"timestamp": s.timestamp, "text": s.text}
                            for s in parsed
                        ],
                        "dom_scrape",
                    )
                except Exception as dom_err:
                    # ---- PATH 4: timedtext baseUrl + bgutil-PoT ----
                    direct_segments: list[dict] | None = None
                    if selected and selected.base_url:
                        try:
                            po_token = await _get_caption_po_token(page)
                            direct_segments = await _fetch_transcript_direct(
                                page, selected.base_url,
                                po_token = po_token,
                            )
                        except Exception as direct_err:
                            log.info(
                                f"[transcript-service] {video_id} direct-API "
                                f"fallback failed: {type(direct_err).__name__}: "
                                f"{str(direct_err)[:120]}"
                            )
                    if not direct_segments:
                        # All paths failed — surface the DOM error so
                        # the existing telemetry/log fingerprints stay
                        # diagnostic.
                        raise dom_err
                    return _ok(
                        direct_segments,
                        "direct_api",
                        note = f" (DOM fell back after: {str(dom_err)[:80]})",
                    )
            except Exception as e:
                reuse_context = False
                self._total_errors += 1
                elapsed = time.time() - start_time
                error_str = str(e)
                log.error(
                    f"[transcript-service] FAIL {video_id} "
                    f"time={elapsed:.2f}s error={error_str[:100]}",
                )
                return {
                    "video_id": video_id,
                    "error":    error_str,
                }
            finally:
                async with self._active_ops_lock:
                    self._active_ops -= 1
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                await self._release_context(context, reuse = reuse_context)

    async def fetch_batch(
        self,
        video_ids:     list[str],
        prefer_manual: bool = True,
        on_video_done: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch transcripts for multiple videos with batch retry strategy.

        Direct port of helpers.py:L1618-1723.

        `on_video_done(video_id, result)` (2026-06-10) fires the instant a
        video reaches a TERMINAL state — success, or a failure that won't be
        retried — in COMPLETION order, not after the whole pass. This is what
        makes the caller's progress bar advance 1/N → 2/N live: the old
        `asyncio.gather` returned every result of a pass at once, so a ≤chunk-
        size run (e.g. 4 videos in one chunk) reported 0→100 in a single
        burst. Retryable failures do NOT fire (the video isn't done yet); it
        fires on the retry pass that resolves it. Each video fires exactly
        once."""
        if not self._initialized:
            raise RuntimeError(
                "PlaywrightTranscriptService not initialized. "
                "Call initialize() first.",
            )
        batch_size = len(video_ids)
        log.info(
            f"[transcript-service] Batch started: {batch_size} videos "
            f"(max_concurrent={self.max_concurrent}, "
            f"batch_retries={self.max_retries})",
        )
        start_time = time.time()
        results_map: dict[str, dict[str, Any]] = {}

        def is_retryable(error_msg: str) -> bool:
            error_lower = error_msg.lower()
            if any(p in error_lower for p in PERMANENT_ERRORS):
                return False
            return any(r in error_lower for r in RETRYABLE_ERRORS)

        pending_ids = list(video_ids)
        for pass_num in range(self.max_retries + 1):
            if not pending_ids:
                break
            pass_label = (
                "First pass"
                if pass_num == 0
                else f"Retry pass {pass_num}"
            )
            log.info(
                f"[transcript-service] {pass_label}: "
                f"{len(pending_ids)} videos",
            )
            # Stream completions in finish order (was: gather → process all
            # at once). Each wrapped coroutine returns (vid, result) so
            # `as_completed` can attribute it; terminal videos fire
            # `on_video_done` immediately for a live progress bar.
            async def _attempt(vid: str) -> tuple[str, dict[str, Any]]:
                try:
                    res = await self._fetch_single_attempt(
                        vid, prefer_manual, pass_num,
                    )
                except Exception as e:  # noqa: BLE001 — normalize to error dict
                    res = {"video_id": vid, "error": str(e)}
                return vid, res

            tasks = [
                asyncio.ensure_future(_attempt(vid)) for vid in pending_ids
            ]
            next_pending: list[str] = []
            for fut in asyncio.as_completed(tasks):
                vid, result = await fut
                terminal = True
                if "error" not in result:
                    results_map[vid] = result
                else:
                    error_msg = result.get("error", "")
                    if is_retryable(error_msg) and pass_num < self.max_retries:
                        next_pending.append(vid)
                        terminal = False
                    else:
                        results_map[vid] = result
                if terminal and on_video_done is not None:
                    try:
                        on_video_done(vid, result)
                    except Exception as cb_err:  # noqa: BLE001
                        log.warning(
                            f"[transcript-service] on_video_done raised: "
                            f"{type(cb_err).__name__}: {cb_err}"
                        )
            pending_ids = next_pending
            if pending_ids and pass_num < self.max_retries:
                cooldown = 3 + pass_num * 2
                log.info(
                    f"[transcript-service] {len(pending_ids)} retryable "
                    f"failures, waiting {cooldown}s before retry",
                )
                await asyncio.sleep(cooldown)
        results = [
            results_map.get(
                vid, {"video_id": vid, "error": "Not processed"},
            )
            for vid in video_ids
        ]
        elapsed = time.time() - start_time
        success = sum(1 for r in results if "error" not in r)
        avg_time = elapsed / batch_size if batch_size > 0 else 0
        log.info(
            f"[transcript-service] Batch complete: {success}/{batch_size} OK "
            f"time={elapsed:.1f}s avg={avg_time:.1f}s/video",
        )
        # Cleanup contexts after batch to prevent memory accumulation
        await self._cleanup_contexts()
        return results


# =============================================================================
# Module-level singleton + cache-aware batch driver
# =============================================================================
_transcript_service: PlaywrightTranscriptService | None = None


def get_transcript_service() -> PlaywrightTranscriptService:
    """Get the global transcript service instance (lazy)."""
    global _transcript_service
    if _transcript_service is None:
        _transcript_service = PlaywrightTranscriptService()
    return _transcript_service


async def init_transcript_service(
    max_concurrent:           int        = MAX_CONCURRENT,
    context_pool_size:        int | None = None,
    navigation_timeout_ms:    int        = NAVIGATION_TIMEOUT_MS,
    browser_refresh_interval: int        = BROWSER_REFRESH_INTERVAL,
    max_retries:              int        = MAX_RETRIES,
) -> PlaywrightTranscriptService:
    """Initialize the global transcript service. Call from Celery task setup
    (deprecated had FastAPI lifespan handle it; Celery is sync so each task
    wraps with asyncio.run + this init/close pair)."""
    global _transcript_service
    _transcript_service = PlaywrightTranscriptService(
        max_concurrent           = max_concurrent,
        context_pool_size        = context_pool_size,
        navigation_timeout_ms    = navigation_timeout_ms,
        browser_refresh_interval = browser_refresh_interval,
        max_retries              = max_retries,
    )
    await _transcript_service.initialize()
    return _transcript_service


async def close_transcript_service() -> None:
    """Close the global transcript service."""
    global _transcript_service
    if _transcript_service:
        await _transcript_service.close()
        _transcript_service = None


# =============================================================================
# ES cache check + batch driver (helpers.py:L556-741)
# =============================================================================
async def _check_existing_transcriptions(
    es_client: AsyncElasticsearch | None,
    video_ids: list[str],
    languages: list[str] | None = None,
) -> dict[str, set[str]]:
    """Query ES `coelhonexus-youtube-transcriptions` for already-indexed
    docs and return `{video_id: {lang, ...}}`."""
    if not es_client or not video_ids:
        return {}
    try:
        result = await es_client.search(
            index   = INDEX_TRANSCRIPTIONS,
            query   = {"terms": {"video_id": video_ids}},
            _source = ["video_id", "lang"],
            size    = len(video_ids) * 10,  # up to 10 langs per video
        )
        existing: dict[str, set[str]] = {}
        for hit in result.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            vid = source.get("video_id")
            lang = source.get("lang")
            if vid and lang:
                existing.setdefault(vid, set()).add(lang)
        return existing
    except Exception as e:
        log.warning(f"[transcription-cache] ES lookup failed: {e}")
        return {}


def _needs_transcription(
    existing_langs: set[str],
    languages:      list[str] | None = None,
) -> bool:
    """True if we still need to fetch — `None` means any existing lang
    is fine."""
    if not existing_langs:
        return True
    if languages is None:
        return False
    for lang in languages:
        # Prefix match: "en" matches "en-US", "en-GB"
        found = any(
            existing_lang.startswith(lang) or lang.startswith(existing_lang)
            for existing_lang in existing_langs
        )
        if not found:
            return True
    return False


async def fetch_transcriptions_batch(
    video_ids:          list[str],
    transcript_service: PlaywrightTranscriptService | None = None,
    es_client:          AsyncElasticsearch | None         = None,
    languages:          list[str] | None                  = None,
    chunk_size:         int                               = DEFAULT_CHUNK_SIZE,
    video_metadata:     dict[str, dict[str, Any]] | None  = None,
    progress_cb:        Callable[[int, int, str | None, bool], None] | None = None,
    stats:              dict[str, int] | None             = None,
) -> list[dict[str, Any]]:
    """Fetch transcriptions for videos with ES caching + chunked processing.

    Strategy (helpers.py:L623-741):
      1. ES cache lookup — skip videos with existing transcriptions
      2. Chunk processing — process in batches of `chunk_size` for
         crash resilience (each chunk is indexed immediately)
      3. Playwright CDP via the supplied / global `transcript_service`

    Returns list of transcription docs ready for ES bulk indexing.
    `stats` (optional out-dict) gets populated with
    `{cached, fetched_ok, fetched_failed, no_transcript}` so the caller
    can surface the per-run breakdown in its result envelope (Phase A
    hint on the Ingest page distinguishes "cached" from "newly indexed"
    from "fetch failed" from "no transcript" — the last one is the
    video-has-no-captions permanent case, NOT an infra failure)."""
    def _set_stats(
        cached: int, ok: int, failed: int, no_transcript: int = 0,
    ) -> None:
        if stats is None:
            return
        stats["cached"] = cached
        stats["fetched_ok"] = ok
        stats["fetched_failed"] = failed
        stats["no_transcript"] = no_transcript

    if not video_ids:
        _set_stats(0, 0, 0)
        return []
    existing_transcriptions = await _check_existing_transcriptions(
        es_client, video_ids, languages,
    )
    ids_to_fetch: list[str] = []
    cached_ids:   list[str] = []
    for vid in video_ids:
        existing_langs = existing_transcriptions.get(vid, set())
        if _needs_transcription(existing_langs, languages):
            ids_to_fetch.append(vid)
        else:
            cached_ids.append(vid)
            log.info(
                f"[transcription-cache] HIT {vid} langs={existing_langs}",
            )
    cached_count = len(cached_ids)
    total_videos = len(video_ids)
    if cached_count > 0:
        log.info(
            f"[fetch_transcriptions_batch] Cache: {cached_count} hits, "
            f"{len(ids_to_fetch)} to fetch",
        )
    # Emit one per-video progress callback for EACH cached id, in order,
    # using `total_videos` as the denominator. Before this change the
    # transcripts bar jumped 2% → 100% in cached-only runs because the
    # function returned early without ever firing progress_cb. The bar
    # advance is genuinely instant for cached entries (no LLM call), but
    # this gives the JS poller at least one payload per cached id with
    # the right (current/total) shape + completed_ids[] entry, so the
    # per-store cell in the drawer flips Queued→Done as expected.
    n_progressed = 0
    if progress_cb:
        for vid in cached_ids:
            n_progressed += 1
            try:
                progress_cb(n_progressed, total_videos, vid, True)
            except Exception as cb_err:
                log.warning(
                    f"[fetch_transcriptions_batch] cached progress_cb raised: "
                    f"{type(cb_err).__name__}: {cb_err}"
                )
    if not ids_to_fetch:
        log.info(
            "[fetch_transcriptions_batch] All videos cached, no fetch needed",
        )
        _set_stats(cached_count, 0, 0)
        return []
    service = transcript_service or _transcript_service
    if not service or not service._initialized:
        log.error(
            "[fetch_transcriptions_batch] Playwright service not available",
        )
        _set_stats(cached_count, 0, len(ids_to_fetch))
        return []
    total_to_fetch = len(ids_to_fetch)
    num_chunks = (total_to_fetch + chunk_size - 1) // chunk_size
    log.info(
        f"[fetch_transcriptions_batch] Fetching {total_to_fetch} videos "
        f"in {num_chunks} chunks of {chunk_size}",
    )
    transcription_docs: list[dict[str, Any]] = []
    total_success = 0
    total_failed = 0
    total_no_transcript = 0
    # Live per-video progress: fired by `fetch_batch` the instant each
    # video reaches a terminal state (completion order), so the bar
    # advances 1/N → 2/N as transcripts land instead of jumping 0→100
    # when the chunk's gather returns. `fetched_emitted` persists across
    # chunks and continues from the cached-id count (`n_progressed`).
    fetched_emitted = 0

    def _on_video_done(vid: str, result: dict[str, Any]) -> None:
        nonlocal fetched_emitted
        fetched_emitted += 1
        if not progress_cb:
            return
        ok = "error" not in result and bool(result.get("page_content"))
        try:
            progress_cb(
                n_progressed + fetched_emitted, total_videos, vid, ok,
            )
        except Exception as cb_err:
            log.warning(
                f"[fetch_transcriptions_batch] live progress_cb raised: "
                f"{type(cb_err).__name__}: {cb_err}"
            )

    for chunk_num in range(num_chunks):
        start_idx = chunk_num * chunk_size
        end_idx = min(start_idx + chunk_size, total_to_fetch)
        chunk_ids = ids_to_fetch[start_idx:end_idx]
        log.info(
            f"[fetch_transcriptions_batch] Chunk {chunk_num + 1}/{num_chunks}: "
            f"{len(chunk_ids)} videos",
        )
        chunk_results = await service.fetch_batch(
            chunk_ids, prefer_manual = True, on_video_done = _on_video_done,
        )
        chunk_docs: list[dict[str, Any]] = []
        for result in chunk_results:
            vid = result.get("video_id")
            if not vid:
                continue
            if "error" not in result and result.get("page_content"):
                lang = result.get("language", "unknown")
                content = result.get("page_content", "")
                is_auto = result.get("is_auto_generated", True)
                meta = (video_metadata or {}).get(vid, {})
                doc = {
                    "id":            f"{vid}_{lang}",
                    "video_id":      vid,
                    "lang":          lang,
                    "content":       content,
                    "is_auto":       is_auto,
                    "method":        result.get("method", "dom_scrape"),
                    "channel_id":    meta.get("channel_id"),
                    "playlist_id":   meta.get("playlist_id"),
                    "_extracted_at": datetime.utcnow().isoformat(),
                }
                chunk_docs.append(doc)
                transcription_docs.append(doc)
                total_success += 1
                log.info(
                    f"[fetch_transcriptions_batch] OK {vid} lang={lang} "
                    f"auto={is_auto} len={len(content)}",
                )
            elif result.get("no_transcript"):
                # Permanent: video has no captions (or is unplayable for
                # this session). Expected outcome, not an infra failure.
                total_no_transcript += 1
                log.info(
                    f"[fetch_transcriptions_batch] NO-TRANSCRIPT {vid}: "
                    f"{result.get('error', '')[:100]}",
                )
            else:
                total_failed += 1
                log.warning(
                    f"[fetch_transcriptions_batch] FAIL {vid}: "
                    f"{result.get('error', '')[:100]}",
                )
            # NOTE: per-video progress is now emitted LIVE by
            # `_on_video_done` (passed into `fetch_batch`) the instant
            # each video finishes — NOT here, where the whole chunk's
            # results are already in hand and would fire in one burst
            # (the 0→100 jump). This loop only builds docs + tallies
            # stats for the result envelope.
        # Index chunk results immediately (crash resilience)
        if chunk_docs and es_client:
            try:
                await index_transcriptions_to_elasticsearch(
                    es_client, chunk_docs,
                )
                log.info(
                    f"[fetch_transcriptions_batch] Chunk "
                    f"{chunk_num + 1} indexed: {len(chunk_docs)} docs",
                )
            except Exception as e:
                log.error(
                    f"[fetch_transcriptions_batch] Chunk "
                    f"{chunk_num + 1} index error: {e}",
                )
        log.info(
            f"[fetch_transcriptions_batch] Chunk {chunk_num + 1}/{num_chunks} "
            f"complete: {total_success} OK, {total_failed} failed, "
            f"{total_no_transcript} no-transcript so far",
        )
    _set_stats(
        cached_count, total_success, total_failed, total_no_transcript,
    )
    log.info(
        f"[fetch_transcriptions_batch] Complete: "
        f"{total_success}/{total_to_fetch} fetched, "
        f"{total_failed} failed, {total_no_transcript} no-transcript, "
        f"{cached_count} cached",
    )
    return transcription_docs
