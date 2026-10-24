"""infra/playwright — access to the COELHO Cloud Playwright browser service (Chromium over CDP).

Every domain reaches the browsers through here; no other module imports
`playwright` / `crawl4ai`, reads the `PLAYWRIGHT_*` / `BROWSER_*` env vars or
resolves a CDP endpoint. `service` holds the CDP helpers and the Playwright
driver start; the `crawl4ai/` submodule holds the Crawl4AI library bound to the
same browsers. Domains keep owning what they do in the browser (selectors,
resource blocking, run configs)."""
from . import crawl4ai, domain, params, service


__all__ = ["crawl4ai", "domain", "params", "service"]
