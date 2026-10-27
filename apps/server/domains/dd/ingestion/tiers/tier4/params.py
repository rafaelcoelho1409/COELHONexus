from __future__ import annotations


USER_AGENT     = "COELHONexus-DocsDistiller-Tier4/1.0"
TIMEOUT_S      = 30.0
CONCURRENCY    = 10
MIN_OK_BYTES   = 200
BFS_MAX_DEPTH  = 3

DISCOVERY_MIN_URLS         = 5
PHASE4A_FAIL_RATE_TRIGGER  = 0.5     # >50% Phase 4a failures → escalate to Playwright

DOCS_PROBES: tuple[str, ...] = (
    "/docs/", "/stable/", "/latest/", "/main/",
    "/v1/", "/en/", "/guide/", "/documentation/",
)

SPA_BODY_MIN     = 1500
SPA_TEXT_MIN     = 200
SPA_SAMPLE_SIZE  = 3


# One page at a time. The browser is a shared CDP one, and crawl4ai's concurrent sessions race on it: with 4 sessions, 3 of 8 TypeScript
# handbook pages came back with ANOTHER page's HTML (`classes.html` held "Conditional Types"; 28 of the 34 stored pages were wrong, and
# half of the 80 fetched were duplicates of each other). With one session all 8 were right (measured 2026-10-27, pw_race.py).
MAX_SESSION_PERMIT     = 1
PAGE_TIMEOUT_MS        = 60_000
RETRY_PAGE_TIMEOUT_MS  = 90_000
RETRY_DELAY_S          = 2.0
DEFAULT_MIN_OK_BYTES   = 200
