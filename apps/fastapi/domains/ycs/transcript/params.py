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
# Requires the terraform-managed `playwright-headed` Chromium pod to
# have at least ~4Gi memory; with the legacy 2Gi limit, 5 concurrent
# YouTube pages OOMKilled mid-eval (Exit Code 137 → `Target page,
# context or browser has been closed` cascade + ECONNREFUSED window
# during restart). The 2026-06-07 Helm/terraform bump is the
# precondition for keeping this at 5.
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
# Resource blocking — 1:1 with the 2026-03-27 gold-standard optimized
# transcript script (scripts/optimized_transcript_extraction.py at commit
# f5bff8e). The deprecated helpers.py we originally ported FROM had been
# trimmed down at some point; the gold script's full block list is what
# actually got the "100% success rate" on a 1545-video Capital-Global-
# class corpus, including the videos that fail under partial blocking.
#
# Why these specific extra patterns matter:
#   - HLS manifests/segments (*.m3u8, *.ts, manifest*): even when video
#     playback is aborted, YouTube fires manifest probes that hold
#     `wait_until="load"` open and delay the engagement-panel render.
#   - recommendations (browse/feed/youtubei/v1/{browse,next,search}):
#     the YouTube UI lazy-loads these AFTER the transcript panel button
#     is clicked, competing for render frames. Blocking them lets the
#     transcript-segment-view-model elements paint immediately.
#   - images (*.jpg/png/gif/webp, ytimg, ggpht): not needed for
#     transcripts; the GET stalls were the second-largest source of
#     "panel not loaded after 15 attempts" failures.
#   - log_interaction / youtubei/v1/log*: same stall pattern as stats.
# =============================================================================
BLOCK_PATTERNS = (
    # VIDEO/AUDIO STREAMING (biggest speedup: 2-5s)
    "**/videoplayback*",
    "**/googlevideo.com/*",
    "**/*.googlevideo.com/*",
    "**/*.m3u8",              # HLS manifests
    "**/*.ts",                # HLS segments
    "**/manifest*",           # DASH manifests
    # ADS (safe to block completely)
    "**/doubleclick.net/*",
    "**/googleadservices.com/*",
    "**/googlesyndication.com/*",
    "**/googleads.g.doubleclick.net/*",
    "**/youtube.com/pagead/*",
    "**/adservice.google.com/*",
    "**/ads?*",
    "**/pagead*",
    # ANALYTICS / TRACKING / LOGGING (no transcript dependency)
    "**/google-analytics.com/*",
    "**/googletagmanager.com/*",
    "**/youtube.com/api/stats/*",
    "**/youtube.com/ptracking*",
    "**/s.youtube.com/*",
    "**/youtubei/v1/log*",
    "**/log_interaction*",
    # RECOMMENDATIONS / BROWSE (not needed — and they compete with the
    # transcript-panel render under YouTube's lazy-paint scheduler)
    "**/browse_ajax*",
    "**/guide_ajax*",
    "**/feed/*",
    # IMAGES (not needed for transcripts; 200-500ms cumulative speedup
    # and prevents the heavy-DOM "panel not loaded" failure mode)
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.png",
    "**/*.gif",
    "**/*.webp",
    "**/yt3.ggpht.com/*",
    "**/i.ytimg.com/*",
)

# Expanded from the originally-ported `{"media"}` per the gold-standard
# script — image + font abort reduces background paint pressure that
# previously delayed the transcript-segment-view-model render past the
# 15s polling budget on slow-channel videos.
BLOCK_RESOURCE_TYPES: frozenset[str] = frozenset({"media", "image", "font"})


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
