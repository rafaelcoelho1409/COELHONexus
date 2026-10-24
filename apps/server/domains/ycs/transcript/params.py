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
# 2026-09-13: trial bump 5→6 — per-page memory footprint should be lower now
# thanks to the same day's blocklist expansion (stylesheets/fonts/peripheral
# player JS). Live-monitored via `kubectl top pod -n playwright --containers`
# during a real batch before keeping this; revert to 5 if chromium's RSS
# creeps toward the 4Gi limit.
MAX_CONCURRENT = 6
CONTEXT_POOL_SIZE = 6  # match max_concurrent → no creation storms
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
    # 2026-09-13 speed pass: webfont CSS + icon SVGs served from these
    # domains (neither is caught by BLOCK_RESOURCE_TYPES — the CSS request
    # itself is resourceType "stylesheet", not "font").
    "**/fonts.googleapis.com/*",
    "**/fonts.gstatic.com/*",
    # Ad-conversion pixels not covered by the narrower pagead/* patterns
    # above (those have a single-`*` tail that doesn't cross further `/`
    # segments; these fire as .../pagead/1p-user-list/<id>/?... etc.).
    "**/google.com/pagead/**",
    "**/google.com.br/pagead/**",
    # Chromecast sender SDK — irrelevant, no cast targets on a datacenter
    # headed instance.
    "**/cast_sender.js",
    # Player-feature JS bundles verified (2026-09-13, live DOM inspection)
    # to load via separate <script src> tags, fully independent of the
    # INLINE <script> tags that set `ytcfg`/`ytInitialPlayerResponse` —
    # blocking these cannot affect either global. None of captions/
    # miniplayer/endscreen/annotations/remote(cast)/offline/lottie are
    # touched by the get_panel/get_transcript in-page-fetch paths.
    "**/player_es6.vflset/**/captions.js",
    "**/player_es6.vflset/**/miniplayer.js",
    "**/player_es6.vflset/**/endscreen.js",
    "**/player_es6.vflset/**/annotations_module.js",
    "**/player_es6.vflset/**/remote.js",
    "**/player_es6.vflset/**/offline.js",
    "**/lottie-light.vflset/**",
)

# image + font abort reduces background paint pressure that delayed
# transcript-segment-view-model render. "stylesheet" added 2026-09-13 —
# CSS/layout is never read by the fetch-based extraction paths.
BLOCK_RESOURCE_TYPES: frozenset[str] = frozenset({
    "media", "image", "font", "stylesheet",
})


PERMANENT_ERRORS = (
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
    # 2026-09-14: "0 caption tracks" read from `ytInitialPlayerResponse`
    # used to be PERMANENT — a single page load, single verdict, no
    # second look. That's a real gap: unlike a deleted/private/region-
    # blocked video (still `"unavailable"`/`"video unavailable"` above,
    # correctly permanent — reloading won't change a server-side
    # playability fact), "0 tracks" is read from the SAME kind of
    # page-load that this file's OWN docstring documents as subject to
    # transient contention under concurrency (see
    # `_wait_for_innertube_context`'s INNERTUBE_CONTEXT race). Moving
    # it here reuses the EXISTING batch-retry-pass loop
    # (`fetch_transcriptions_batch`'s pass/cooldown mechanism, bounded
    # by `MAX_RETRIES`) instead of trusting one load's reading —
    # matches `"no caption tracks"` in the exact message
    # `service.py::_fetch_single_attempt` returns for this case.
    "no caption tracks",
)

# 2026-09-14: long-video partitioning. Below this threshold, a video is
# stored as a single ES document exactly as before — this only engages
# for the small minority of videos where a full-transcript single-shot
# LLM extraction genuinely risks the rotator's wall-clock budget (see
# `docs/YCS-NEO4J-RELIABILITY-2026-09-13.md`'s 2h04m podcast case:
# every other video in that batch was 14-19 min and succeeded cleanly,
# only the outlier over an hour needed this). 1800s = 30 min, the
# largest length observed to work reliably at a single shot so far.
SPLIT_THRESHOLD_S = 30 * 60
# Aim for partitions around this size — NOT a hard cap; actual cut
# points land on the nearest real caption-silence gap (see
# `domain.split_segments_by_gap`), so a partition can run a bit shorter
# or longer than this depending on where speech actually pauses.
SPLIT_TARGET_PARTITION_S = 20 * 60
# How far a cut point may drift from its ideal boundary while
# searching for the largest silence gap — keeps one huge pause near
# the start from swallowing every cut instead of just its own.
SPLIT_SEARCH_WINDOW_RATIO = 0.3
# Trailing words from partition N carried as a prefix into partition
# N+1's extraction text — standard chunking-with-overlap, gives the
# LLM a few seconds of context across the cut instead of starting cold
# (does not affect partition N's own stored segments/duration, only
# the flattened text partition N+1 is extracted from).
SPLIT_OVERLAP_WORDS = 120
