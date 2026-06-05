from __future__ import annotations


# --------------------------------------------------------------------------- #
# run.py — httpx fetch + BFS + SPA detection
# --------------------------------------------------------------------------- #
USER_AGENT     = "COELHONexus-DocsDistiller-Tier4/1.0"
TIMEOUT_S      = 30.0
CONCURRENCY    = 10
MIN_OK_BYTES   = 200
BFS_MAX_DEPTH  = 3

# Trigger thresholds for Phase 1 (Crawl4AI seeder) and Phase 3 (SPA gate).
DISCOVERY_MIN_URLS         = 5
PHASE4A_FAIL_RATE_TRIGGER  = 0.5     # >50% Phase 4a failures → escalate to Playwright

# Common docs subtree probes used by Phase 0 seed enrichment.
DOCS_PROBES: tuple[str, ...] = (
    "/docs/", "/stable/", "/latest/", "/main/",
    "/v1/", "/en/", "/guide/", "/documentation/",
)

# SPA-detection thresholds.
SPA_BODY_MIN     = 1500
SPA_TEXT_MIN     = 200
SPA_SAMPLE_SIZE  = 3


# --------------------------------------------------------------------------- #
# playwright.py — Crawl4AI remote-CDP fallback
# --------------------------------------------------------------------------- #
MAX_SESSION_PERMIT     = 4              # context-race sweet spot on shared CDP
PAGE_TIMEOUT_MS        = 60_000
RETRY_PAGE_TIMEOUT_MS  = 90_000
RETRY_DELAY_S          = 2.0
DEFAULT_MIN_OK_BYTES   = 200
