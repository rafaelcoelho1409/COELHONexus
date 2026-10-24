"""infra/playwright — access to the COELHO Cloud Playwright browser service (Chromium over CDP).

Every domain reaches the browsers through here; no other module imports
`playwright` / `crawl4ai`, reads the `PLAYWRIGHT_*` / `BROWSER_*` env vars or
resolves a CDP endpoint. `service` holds the CDP helpers and the Playwright
driver start; `crawler` holds the Crawl4AI building blocks bound to the same
browsers. `crawler` is NOT imported here — it pulls in crawl4ai, so callers opt
in with `import infra.playwright.crawler`. Domains keep owning what they do in
the browser (selectors, resource blocking, run configs)."""
from . import domain, params, service


__all__ = ["domain", "params", "service"]
