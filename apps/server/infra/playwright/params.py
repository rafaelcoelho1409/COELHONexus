"""Playwright browser-service endpoints (CDP over HTTP) + CDP helper tunables."""
from __future__ import annotations

import os


# Headless Chromium sidecar. Unset/blank means "no remote browser" — the crawler then falls
# back to a local Chromium.
CDP_HEADLESS = os.environ.get("PLAYWRIGHT_CDP_HEADLESS", "").strip()

# YouTube blocks headless captions extraction; HEADED is the only working path.
CDP_HEADED = os.environ.get(
    "PLAYWRIGHT_CDP_HEADED",
    "http://playwright.playwright.svc.cluster.local:9222",
)

CDP_PROBE_TIMEOUT_S = 10   # /json/version, /json/list
CDP_CLOSE_TIMEOUT_S = 5    # /json/close/<id>

# Non-page targets that pile up on the long-lived sidecar and crash `connect_over_cdp`.
STALE_TARGET_TYPES = (
    "service_worker",
    "dedicated_worker",
    "shared_worker",
    "background_page",
)
