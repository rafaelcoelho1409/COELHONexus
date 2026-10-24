"""infra/playwright/crawl4ai — the Crawl4AI library, bound to the Playwright browser service.

Crawl4AI drives the Playwright browsers (remote headless CDP sidecar, or a local
Chromium fallback); its URL seeder needs no browser at all. Domains reach all of it
through here, so no other module imports `crawl4ai`."""
from . import params, service


__all__ = ["params", "service"]
