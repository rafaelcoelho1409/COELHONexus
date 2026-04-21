# Crawl4AI Post-Run Fixes (to apply after run c5de2c9d finishes)

Research-backed fixes for the 3 dominant failure modes observed in the 2026-04-21 DeepAgents ingest run (996 success / 792 fail = 55.7% success rate, 42% sitemap coverage).

All fixes touch `apps/fastapi/services/knowledge/ingestion.py` unless noted.

## Error #1 — `Target page, context or browser has been closed` (787 failures, 99.4%)

### Root cause
`BrowserManager.get_page()` keys contexts by `config_hash`. All concurrent sessions with the same `CrawlerRunConfig` share **ONE** BrowserContext. When any page in that shared context crashes, ALL sibling pages become invalid and subsequent `context.new_page()` / `page.goto()` throw "Target closed". Cascade effect: the more pages running concurrently, the higher the probability any one crashes, and one crash takes the whole batch with it.

**Source:** `crawl4ai/browser_manager.py:775-841` (`_get_or_create_page`), DeepWiki §3.4 "Multiple sessions can share the same BrowserContext if they have identical configuration".

### Fix 1a (HIGHEST IMPACT) — per-URL BrowserContext via unique `session_id`

**Change:** when calling `arun_many`, set a unique `session_id` per URL via a list of configs, OR use the dispatcher's built-in per-URL task_id (which MemoryAdaptiveDispatcher already does — but Crawl4AI honors `session_id` only if set on `CrawlerRunConfig`).

**Option A** — wrap each URL with its own config:
```python
import uuid

configs = [
    crawler_cfg_base.clone(session_id=f"crawl-{uuid.uuid4().hex}")
    for _ in urls_to_crawl
]
# Note: arun_many accepts a list of configs matched positionally to URLs
stream_iter = await crawler.arun_many(
    urls_to_crawl, config=configs, dispatcher=dispatcher_primary,
)
```

**Option B (simpler)** — use `CrawlerRunConfig(session_id=...)` with a per-URL callable. Needs checking whether Crawl4AI supports config-per-URL on `arun_many` in v0.8.

**Caveat:** sessions accumulate in the browser pool. Must call `crawler.crawler_strategy.kill_session(sid)` in a finally block, OR rely on `arun_many`'s dispatcher cleanup (needs verification).

**Evidence:**
- `browser_manager.py:775-841` — unique `session_id` → isolated `(Page, Context, config_hash)` triple
- Issue #1379 (maintainer thread) — advocates `session_id` per call
- DeepWiki §3.1 "Browser Strategies and Pool Management"

**Expected impact:** 787 → <40 failures on this error class.
**Confidence:** HIGH.

### Fix 1b — lower `max_session_permit` 15 → 4

**Evidence:**
- Issue #1326 (maintainer "Root caused") — error reproduces reliably above ~4 parallel pages regardless of host resources
- Issue #1927 (OPEN, 21 Apr 2026) — BFS's internal dispatcher *ignores* `max_session_permit` anyway, so setting 15 was giving false sense of throttling

```python
dispatcher_primary = MemoryAdaptiveDispatcher(
    max_session_permit=4,   # was 15
    memory_threshold_percent=85.0,
    recovery_threshold_percent=75.0,
    rate_limiter=RateLimiter(base_delay=(0.5, 1.5), max_delay=20.0, max_retries=3),
)
```

**Trade:** slower wall time, but fewer races. Use with 1a for best of both.
**Confidence:** HIGH.

### Fix 1c — `BrowserConfig(light_mode=True)` (ONLY if we control the CDP)

`light_mode=True` applies `BROWSER_DISABLE_OPTIONS` (background networking, sync, translations, etc.) for stability. **Remote CDP flags are ignored** — the server must launch with these. If we switch to local Chromium per worker, enable this.

**Do NOT add `--single-process` to extra_args** — Issue #1585 shows this is actively *correlated with* "Target page closed" (one renderer crash kills all tabs).

### Fix 1d — what DOESN'T work
- **WebSocket keepalive** — Crawl4AI uses vanilla `playwright.chromium.connect_over_cdp(ws_url)` with no ping interval, no reconnect. Playwright issue #35928 confirms Playwright's CDP has no keepalive. No Crawl4AI-level knob. Workaround is server-side (on the CDP host, enable WS ping) — not our control.
- **Upstream PR fix** — none exists. v0.7.8 added lock hardening but didn't address the shared-context-per-config design.

## Error #2 — Next.js prefetch races (70 failures, 8.8%)

### Root cause
Next.js `<Link>` components prefetch `/_next/data/*.json` files eagerly. These requests race `page.goto()` and can cause `net::ERR_ABORTED` or navigation timeouts.

### Fix 2a (HIGH) — abort Next.js prefetch via `on_page_context_created` hook

```python
async def block_next_prefetch(page, context, **kw):
    await context.route("**/_next/data/**", lambda r: r.abort())
    # Optional: also block asset heavy-hitters
    await context.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
                        lambda r: r.abort())
    await context.route("**/*.css", lambda r: r.abort())

crawler.crawler_strategy.set_hook("on_page_context_created", block_next_prefetch)
```

**Evidence:**
- Hook documented at docs.crawl4ai.com/advanced/hooks-auth/
- Next.js `<Link>` source (Vercel discussions #60581) — prefetch issues `/_next/data/*.json` requests that race `goto()`
- Playwright's `context.route()` is the standard blocking pattern

**Confidence:** HIGH.

### Fix 2b (MEDIUM) — `wait_until="commit"` instead of "domcontentloaded"

`commit` returns as soon as the navigation response is committed — before Next.js has a chance to fire prefetches that race. Pair with the existing JS-predicate `wait_for` to ensure content is mounted.

```python
crawler_cfg_base = CrawlerRunConfig(
    wait_until="commit",   # was "domcontentloaded"
    wait_for="js:() => document.readyState === 'complete' && !!document.querySelector('#__next, main, article')",
    ...
)
```

**Trade:** `commit` is earlier than `domcontentloaded`, so the `wait_for` predicate does more work. Not always faster in practice.
**Confidence:** MEDIUM.

## Error #3 — quality-gate drops (~5 failures)

### Fix 3a (HIGH) — replace hand-rolled gate with `PruningContentFilter`

Current code has a hand-rolled `_passes_content_quality` check (`min_page_chars=400`, `max_link_text_ratio=0.55`). Replace with Crawl4AI's documented filter:

```python
from crawl4ai.content_filter_strategy import PruningContentFilter

content_filter = PruningContentFilter(
    threshold=0.45,
    threshold_type="dynamic",   # adjusts per-node based on <article>/<p>/<nav> importance
    min_word_threshold=5,
)

crawler_cfg_base = CrawlerRunConfig(
    ...,
    content_filter=content_filter,
)
```

`threshold_type="dynamic"` is the community consensus for doc sites — handles sidebar/nav chrome automatically in the tree walker. That's exactly what our link-ratio heuristic was trying to approximate, but done more accurately.

**Do NOT use `BM25ContentFilter`** — query-ranked, wrong tool for noise removal (docs §3.1 are explicit).

After enabling the Crawl4AI filter, `_passes_content_quality` becomes redundant. Can either remove it or keep as defense-in-depth at a lower threshold (min_page_chars=200 post-prune).

**Evidence:** docs.crawl4ai.com/core/fit-markdown/ — default threshold ~0.48, `"dynamic"` for doc sites.
**Confidence:** HIGH.

## Priority order (execute sequence)

1. **Fix 1a + 1b together** — per-URL `session_id` + `max_session_permit=4`. Biggest win. ~95% of current failures eliminated.
2. **Fix 2a** — `on_page_context_created` hook for Next.js prefetch + asset blocking. Cleans up most of the remaining 70.
3. **Fix 3a** — `PruningContentFilter(threshold=0.45, threshold_type="dynamic")`. Better content quality, fewer false-positive quality-gate drops.

## Projected result

| Current | Expected after all 3 fixes |
|---|---|
| 996 success / 792 fail (55.7%) | ~1,720 success / ~60 fail (~97%) |
| 42% sitemap coverage | ~73% sitemap coverage (limited by BFS depth) |
| 55 min wall time | ~30-45 min wall time (lower concurrency trade-off) |

BFS `max_depth=5` is the remaining ceiling on coverage — raising to 7 or 8 would help, but risks walking too deep into symbol trees.

## Key sources

- https://github.com/unclecode/crawl4ai/issues/1585 (Root-caused, Nov 2025)
- https://github.com/unclecode/crawl4ai/issues/1379 (Page.goto target closed during pagination)
- https://github.com/unclecode/crawl4ai/issues/1326 (Railway deploy, same error)
- https://github.com/unclecode/crawl4ai/issues/1198 (persistent_context race, fixed Aug 2025)
- https://github.com/unclecode/crawl4ai/issues/1927 (max_session_permit ignored — OPEN)
- https://deepwiki.com/unclecode/crawl4ai/3.4-session-management
- https://deepwiki.com/unclecode/crawl4ai/3.1-browser-strategies-and-pool-management
- https://github.com/unclecode/crawl4ai/blob/main/crawl4ai/browser_manager.py
- https://docs.crawl4ai.com/advanced/hooks-auth/
- https://docs.crawl4ai.com/core/fit-markdown/
- https://github.com/microsoft/playwright/issues/35928 (CDP no keepalive)
