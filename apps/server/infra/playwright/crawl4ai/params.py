"""Crawl4AI tunables — env reads only; the browser endpoints live in `infra.playwright.params`."""
from __future__ import annotations

import os


# Proxy for the local-Chromium fallback only (the remote CDP sidecar has its own network path).
PROXY_URL = os.environ.get("BROWSER_PROXY_URL", "").strip()
