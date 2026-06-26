from __future__ import annotations

import os


CDP_HEADLESS = os.environ.get(
    "PLAYWRIGHT_CDP_HEADLESS",
    "http://playwright.playwright.svc.cluster.local:9224",
)
# YouTube blocks headless captions extraction; HEADED is the only working path.
CDP_HEADED = os.environ.get(
    "PLAYWRIGHT_CDP_HEADED",
    "http://playwright.playwright.svc.cluster.local:9222",
)

# Requires playwright-headed pod with ≥4Gi memory; 5 concurrent pages OOMKilled at 2Gi.
MAX_CONCURRENT = 5
CONTEXT_POOL_SIZE = 5  # match max_concurrent → no creation storms
TIMEOUT_MS = 30000
NAVIGATION_TIMEOUT_MS = 60000
BROWSER_REFRESH_INTERVAL = 15
MAX_RETRIES = 2

CONNECT_TIMEOUT_S = 30.0
INITIAL_RETRY_WAIT_S = 5.0
RETRY_LIMIT = 6               # exponential 5s → ~60s total

DEFAULT_CHUNK_SIZE = 10       # ES checkpoint frequency

# YouTube's timedtext endpoint returns HTTP 200 with empty body without a PoT token;
# keep in sync with the bgutil-pot Helm sidecar port.
POT_PROVIDER_URL = os.environ.get(
    "YCS_POT_PROVIDER_URL", "http://127.0.0.1:4416",
)
POT_REQUEST_TIMEOUT_S = 30.0
POT_CACHE_SLACK_S = 300.0     # re-mint 5 min before expiresAt


BLOCK_PATTERNS = (
    "**/videoplayback*",
    "**/googlevideo.com/*",
    "**/*.googlevideo.com/*",
    "**/*.m3u8",              # HLS manifests
    "**/*.ts",                # HLS segments
    "**/manifest*",           # DASH manifests
    "**/doubleclick.net/*",
    "**/googleadservices.com/*",
    "**/googlesyndication.com/*",
    "**/googleads.g.doubleclick.net/*",
    "**/youtube.com/pagead/*",
    "**/adservice.google.com/*",
    "**/ads?*",
    "**/pagead*",
    "**/google-analytics.com/*",
    "**/googletagmanager.com/*",
    "**/youtube.com/api/stats/*",
    "**/youtube.com/ptracking*",
    "**/s.youtube.com/*",
    "**/youtubei/v1/log*",
    "**/log_interaction*",
    # RECOMMENDATIONS / BROWSE compete with transcript-panel render under YouTube's lazy-paint scheduler.
    "**/browse_ajax*",
    "**/guide_ajax*",
    "**/feed/*",
    # Images not needed; blocking reduces "panel not loaded" failures from render contention.
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.png",
    "**/*.gif",
    "**/*.webp",
    "**/yt3.ggpht.com/*",
    "**/i.ytimg.com/*",
)

# image + font abort reduces background paint pressure that delayed transcript-segment-view-model render.
BLOCK_RESOURCE_TYPES: frozenset[str] = frozenset({"media", "image", "font"})


PERMANENT_ERRORS = (
    "no transcript",
    "unavailable",
    "video unavailable",
    "private video",
)
RETRYABLE_ERRORS = (
    "button not found",
    "panel not loaded",
    "timeout",
    "target closed",
    "navigation",
    "browser",
    "context",
    "expand",
)
