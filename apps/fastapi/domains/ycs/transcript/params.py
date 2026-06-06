"""ycs/transcript — Playwright CDP tunables (env-bound).

Direct port of deprecated `routers/v1/youtube/helpers.py:L748-777`
+ `L1199-1219` (PlaywrightTranscriptService defaults) +
`L1292-1295, L1325-1326` (browser-refresh retry knobs).

The HEADED CDP endpoint is the only path that works — YouTube blocks
headless captions extraction. HEADLESS is kept here for parity (the
deprecated module also exported it)."""
from __future__ import annotations

import os


# =============================================================================
# CDP endpoints (in-cluster ClusterIP services)
# =============================================================================
CDP_HEADLESS = os.environ.get(
    "PLAYWRIGHT_CDP_HEADLESS",
    "http://playwright.playwright.svc.cluster.local:9224",
)
CDP_HEADED = os.environ.get(
    "PLAYWRIGHT_CDP_HEADED",
    "http://playwright.playwright.svc.cluster.local:9222",
)


# =============================================================================
# PlaywrightTranscriptService defaults
# =============================================================================
MAX_CONCURRENT = 5
CONTEXT_POOL_SIZE = 5  # match max_concurrent → no creation storms
TIMEOUT_MS = 30000
NAVIGATION_TIMEOUT_MS = 60000
BROWSER_REFRESH_INTERVAL = 15
MAX_RETRIES = 2


# =============================================================================
# Browser-refresh retry tuning
# =============================================================================
CONNECT_TIMEOUT_S = 30.0      # per CDP connect attempt
INITIAL_RETRY_WAIT_S = 5.0
RETRY_LIMIT = 6               # exponential 5s → ~60s total


# =============================================================================
# fetch_transcriptions_batch
# =============================================================================
DEFAULT_CHUNK_SIZE = 10       # ES checkpoint frequency


# =============================================================================
# Resource blocking (helpers.py:L758-777)
# =============================================================================
BLOCK_PATTERNS = (
    # VIDEO/AUDIO STREAMING (biggest speedup: 2-5s)
    "**/videoplayback*",
    "**/googlevideo.com/*",
    "**/*.googlevideo.com/*",
    # ADS
    "**/doubleclick.net/*",
    "**/googleadservices.com/*",
    "**/googlesyndication.com/*",
    "**/googleads.g.doubleclick.net/*",
    "**/youtube.com/pagead/*",
    # ANALYTICS/TRACKING
    "**/google-analytics.com/*",
    "**/googletagmanager.com/*",
    "**/youtube.com/api/stats/*",
    "**/youtube.com/ptracking*",
    "**/s.youtube.com/*",
)

BLOCK_RESOURCE_TYPES: frozenset[str] = frozenset({"media"})


# =============================================================================
# Error classification (helpers.py:L1650-1665)
# =============================================================================
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
