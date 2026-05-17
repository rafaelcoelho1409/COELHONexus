# Docs Distiller crawler — improvement backlog

Captured 2026-05-17 after a research pass on docs-page crawling best practices.
Architecture decision locked in beforehand: **keep HTTPX as the fetcher; do
not move to Crawl4AI-everywhere**. Per-tier analysis showed Crawl4AI+Playwright
would be 10-30× slower for static HTML (most docs sites) with no quality gain
worth the trade, plus heavier operational risk. Targeted use of Crawl4AI's
strengths is in the priority list below (#5).

Current pipeline (for context):
- Tier 1 — HTTPX single GET on `llms-full.txt`
- Tier 2 — HTTPX llms.txt index + N parallel per-URL GETs
- Tier 3 — HTTPX sitemap.xml + N parallel per-URL GETs
- Tier 4a — HTTPX BFS over docs
- Tier 4b — Playwright via Crawl4AI (escalation only when 4a > 50% failure)
- Tier 5 — HTTPX (GitHub API + raw.githubusercontent.com)
- HTML→md: `extract.py` (BeautifulSoup + markdownify)

## Top 5 prioritized improvements

### 1. Replace `extract.py` with `trafilatura.extract()` — biggest single quality win

**Problem:** BeautifulSoup + markdownify leaks navigation chrome. Concretely,
every Docker page has a leading `Back` line that's a nav breadcrumb, not
content. Same pattern likely affects other modern docs themes (Docusaurus,
Nextra, Mintlify).

**Fix:** swap to `trafilatura` — purpose-built for main-content extraction.
Uses layout heuristics to strip nav/footer/ads/cookie-banners. Already in
v1's pyproject.toml (was only used for `tree_cleaning()`); just expand its
role to do the full conversion.

**Alternative:** Kreuzberg's `html-to-markdown` (Rust core, CommonMark
compliant, faster). Newer (2026), less battle-tested in our context but
worth benchmarking against trafilatura.

**Effort:** small — replace `html_to_markdown()` in `extract.py`. Tier 2/3/4
all use the same extractor, so one change fixes all three tiers.

**Why first:** unblocks the most "missing content / weird formatting"
complaints across the entire pipeline.

---

### 2. Extend Tier 4b's Playwright fallback to Tier 3

**Problem:** JavaScript-rendered SPA docs (Streamlit-style, some Vue/React
docs) return empty `<div id="root">` shells to HTTPX. Tier 3 silently drops
these as sub-`_MIN_OK_BYTES` responses.

**Fix:** mirror the Tier 4b pattern in Tier 3 — when HTTPX returns
suspiciously small content, queue the URL for Playwright retry instead of
dropping. The `playwright_crawl.py` module is already wired and proven on
Tier 4b; same call site works for Tier 3.

**Effort:** small-medium — add a `failed_or_small` list to Tier 3's `_bound`
coroutine, post-gather call `_phase4b_playwright` on it.

**Why second:** recovers entire SPA docs sites that we silently 0-coverage
today. Per the [JS Rendering and AI Crawlers 2026 article](https://www.getpassionfruit.com/blog/javascript-rendering-and-ai-crawlers-can-llms-read-your-spa),
AI crawlers (GPTBot, ClaudeBot) face exactly this problem — solved with
client-side Playwright fallback (which we already do for Tier 4).

---

### 3. Honor `robots.txt` + `Crawl-Delay`

**Problem:** we currently ignore robots.txt entirely. Real risk: getting
IP-banned by hosts that enforce. Per
[ScrapeHero's respectful-crawling guide](https://www.scrapehero.com/rate-limiting-in-web-scraping/),
this is table-stakes for any production crawler.

**Fix:** at tier entry, fetch `{base}/robots.txt`, parse with
`urllib.robotparser`, drop URLs matching `Disallow` rules for our
user-agent, throttle by declared `Crawl-Delay` (if any).

**Effort:** ~30 LoC. One helper module shared across tiers.

**Why third:** hygiene + risk mitigation. Costs nothing in throughput on
sites without strict robots.txt; only kicks in when needed.

---

### 4. `aiometer` for time-based rate limiting

**Problem:** today we cap concurrency at `_CONCURRENCY = 8-10` (semaphores).
But on a fast host, 8 concurrent can burst to 30+ req/s; on a slow host it
bottlenecks at 2 req/s. Same code, very different load profiles.

**Fix:** add `aiometer.run_on_each` for time-rate limiting (e.g., max 10
req/sec per host, regardless of latency). Better citizenship, more
predictable load. Per
[Scrapfly's async rate-limit guide](https://scrapfly.io/blog/posts/how-to-rate-limit-asynchronous-python-requests).

**Effort:** small — wrap `asyncio.gather` calls in tier 2/3/4a.

**Why fourth:** smoother behavior, not a correctness fix.

---

### 5. Per-host 429 + `Retry-After` honor

**Problem:** `tenacity` is wired but doesn't read `Retry-After` headers.
When a site rate-limits us with `429 Retry-After: 60`, we should wait 60s,
not the exponential backoff default.

**Fix:** custom retry decorator that reads the `Retry-After` header on 429
responses and overrides the backoff. ~20 LoC.

**Why fifth:** edge case (most docs sites don't rate-limit aggressively),
but cheap to add.

## Lower-priority (nice-to-have)

### `sitemap_path_filter` catalog field

DuckDB's sitemap has 3083 URLs — many are `/events/`, `/blog/`, `/news/`
(non-docs). A per-framework `sitemap_path_filter: ["/docs/"]` field in
`sources.yaml` would drop non-matching URLs at discovery time. Saves ~30-50%
of fetches on noisy sites + cleaner downstream planner input.

### HTTP caching via `ETag` / `If-None-Match`

Re-crawl/refresh scenarios benefit: send `If-None-Match: <last-etag>` and
treat 304 as "no change, reuse stored body". Currently every refresh
re-downloads everything. Would make incremental updates 10-100× faster on
sites that haven't changed.

### Explicit `httpx.Limits` matching semaphore concurrency

HTTPX default connection pool = 10; if our semaphore allows 32 concurrent
fetches, the pool becomes the bottleneck. Set
`httpx.Limits(max_connections=64, max_keepalive_connections=32)` to match.

### Crawl4AI's `DefaultMarkdownGenerator` standalone

If trafilatura quality plateaus, Crawl4AI's markdown generator can be used
WITHOUT browser (just pipe HTML to it). Better at modern docs layouts
(Docusaurus/Nextra/Mintlify) than trafilatura in some cases. Tier 2/3/4
share extractor → one swap fixes all.

## Sources

- [Scrapfly: Rate-limiting async Python requests](https://scrapfly.io/blog/posts/how-to-rate-limit-asynchronous-python-requests)
- [ScrapeHero: Overcoming rate limiting](https://www.scrapehero.com/rate-limiting-in-web-scraping/)
- [HTML to Markdown for LLMs / RAG best practices 2026](https://www.searchcans.com/blog/html-to-markdown-llm-training-data-best-practices-2026/)
- [Trafilatura docs](https://trafilatura.readthedocs.io)
- [Kreuzberg html-to-markdown](https://github.com/kreuzberg-dev/html-to-markdown)
- [JS rendering + AI crawlers 2026](https://www.getpassionfruit.com/blog/javascript-rendering-and-ai-crawlers-can-llms-read-your-spa)
- [Crawlee for Python — scaling crawlers](https://crawlee.dev/python/docs/guides/scaling-crawlers)
