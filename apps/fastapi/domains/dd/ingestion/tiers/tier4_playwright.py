"""Crawl4AI Playwright fallback — invoked when the static httpx path is
unsafe for the target site (SPA shells detected, fetch fail rate >50%, or
SPA-only frameworks like Next.js docs).

Connects to the remote Chromium-CDP browser pool already deployed in
COELHO Cloud (env: PLAYWRIGHT_CDP_HEADLESS), so this image stays slim
(no embedded Chromium, no `playwright install`). Falls back to local
Chromium if PLAYWRIGHT_CDP_HEADLESS is unset (mostly for local dev).

Tuned from the v3 ingest path:
  - max_session_permit=4 — empirical sweet spot against the "Target page
    closed" race on shared CDP (Crawl4AI #1326).
  - Per-URL session_id — each URL gets a fresh BrowserContext so one dying
    page can't poison siblings (Crawl4AI #1379).
  - on_page_context_created hook blocks _next/data/* + images/fonts/CSS
    to avoid Next.js prefetch races and cut wall time.
  - PruningContentFilter + DefaultMarkdownGenerator drive the markdown
    extraction (tree-aware, replaces our hand-rolled chrome-strip).

Imports are deferred so the heavy crawl4ai dependency only loads when
this fallback is actually used.
"""
import asyncio
import logging
import os
import re
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

from ..cdp import resolve_cdp_ws_url
from ..progress import Progress
from ..storage import Store


logger = logging.getLogger(__name__)


_MAX_SESSION_PERMIT = 4              # context-race sweet spot on shared CDP
_PAGE_TIMEOUT_MS = 60_000
_RETRY_PAGE_TIMEOUT_MS = 90_000
_RETRY_DELAY_S = 2.0
_DEFAULT_MIN_OK_BYTES = 200


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


def _build_browser_config():
    """Build BrowserConfig using remote CDP if available, else local."""
    from crawl4ai import BrowserConfig
    cdp_http = (os.environ.get("PLAYWRIGHT_CDP_HEADLESS") or "").strip() or None
    cdp_ws = resolve_cdp_ws_url(cdp_http) if cdp_http else None
    if cdp_ws:
        logger.info(f"[playwright] using remote CDP {cdp_ws[:80]}…")
        return BrowserConfig(
            browser_type="chromium",
            use_managed_browser=True,
            cdp_url=cdp_ws,
            headless=True,
            verbose=False,
        )
    logger.info("[playwright] CDP unresolved — falling back to local Chromium")
    proxy = (os.environ.get("BROWSER_PROXY_URL") or "").strip() or None
    return BrowserConfig(
        browser_type="chromium",
        headless=True,
        verbose=False,
        proxy=proxy,
    )


def _build_run_configs(cfg_cls, cache_mode, lxml_strategy, md_generator):
    """Return (primary_cfg, retry_cfg)."""
    common = dict(
        cache_mode=cache_mode.BYPASS,
        wait_until="domcontentloaded",
        wait_for=(
            "js:() => document.readyState === 'complete' && "
            "!!document.querySelector('#__next, main, article')"
        ),
        word_count_threshold=50,
        excluded_tags=["nav", "footer", "aside"],
        exclude_external_links=True,
        scraping_strategy=lxml_strategy,
        markdown_generator=md_generator,
        stream=True,
        max_retries=2,
        verbose=False,
    )
    primary = cfg_cls(
        **common,
        delay_before_return_html=0.2,
        page_timeout=_PAGE_TIMEOUT_MS,
    )
    retry = cfg_cls(
        **common,
        delay_before_return_html=2.0,
        page_timeout=_RETRY_PAGE_TIMEOUT_MS,
    )
    return primary, retry


async def _install_resource_blocker(crawler) -> None:
    """Block Next.js prefetch + images/fonts/CSS via Playwright route()."""
    async def _hook(page, context, **_):
        try:
            await context.route("**/_next/data/**", lambda r: r.abort())
            await context.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,otf,mp4,webm}",
                lambda r: r.abort(),
            )
            await context.route("**/*.css", lambda r: r.abort())
        except Exception as e:
            logger.warning(f"[playwright] route-hook failed (non-fatal): {e}")
    try:
        crawler.crawler_strategy.set_hook("on_page_context_created", _hook)
    except Exception as e:
        logger.warning(f"[playwright] could not install route hook: {e}")


def _build_md_generator():
    """PruningContentFilter + DefaultMarkdownGenerator. Falls back to None
    if the content_filter_strategy import is missing on this crawl4ai
    version (callers should still work; extraction is just less aggressive)."""
    try:
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError:
        return None
    return DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(
            threshold=0.45,
            threshold_type="dynamic",
            min_word_threshold=5,
        ),
    )


async def crawl_urls(
    urls: list[str],
    *,
    framework_slug: str,
    progress: Progress,
    store: Store,
    min_ok_bytes: int = _DEFAULT_MIN_OK_BYTES,
) -> tuple[int, list[str]]:
    """Crawl every URL via Playwright + Crawl4AI extraction. Streaming
    consumer writes successes to the store immediately; transient navigation
    failures get a single retry pass with patient timeouts.

    Returns (pages_written, failed_urls).
    """
    if not urls:
        return 0, []

    # Deferred imports — only paid when this path actually runs.
    from crawl4ai import (
        AsyncWebCrawler,
        CacheMode,
        CrawlerRunConfig,
        LXMLWebScrapingStrategy,
    )
    from crawl4ai.async_dispatcher import (
        MemoryAdaptiveDispatcher,
        RateLimiter,
    )

    browser_cfg = _build_browser_config()
    md_generator = _build_md_generator()
    primary_cfg, retry_cfg = _build_run_configs(
        CrawlerRunConfig, CacheMode, LXMLWebScrapingStrategy(), md_generator,
    )

    dispatcher_primary = MemoryAdaptiveDispatcher(
        max_session_permit=_MAX_SESSION_PERMIT,
        memory_threshold_percent=85.0,
        recovery_threshold_percent=75.0,
        check_interval=1.0,
        rate_limiter=RateLimiter(
            base_delay=(0.5, 1.5), max_delay=20.0, max_retries=3,
        ),
    )
    dispatcher_retry = MemoryAdaptiveDispatcher(
        max_session_permit=1,
        memory_threshold_percent=85.0,
        recovery_threshold_percent=75.0,
        check_interval=1.0,
        rate_limiter=RateLimiter(
            base_delay=(1.0, 3.0), max_delay=30.0, max_retries=3,
        ),
    )

    written = 0
    failed: list[str] = []
    transient_nav_failures: list[str] = []

    async def _consume(stream, label: str) -> None:
        nonlocal written
        async for r in stream:
            await progress.raise_if_cancelled()
            url = getattr(r, "url", "?")
            if not getattr(r, "success", False):
                err = str(getattr(r, "error_message", "no detail"))
                transient = any(s in err for s in (
                    "ACS-GOTO", "Timeout", "timeout", "Navigation", "net::ERR",
                ))
                if transient and label == "primary":
                    transient_nav_failures.append(url)
                else:
                    failed.append(url)
                await progress.record_url(
                    url, status="fetch_error", tier="playwright",
                    error_msg=err[:300],
                )
                continue

            # Prefer fit_markdown (pruned) over the raw markdown; fall back
            # if the extractor didn't emit one.
            md_obj = getattr(r, "markdown", None)
            body = ""
            if md_obj is not None:
                body = getattr(md_obj, "fit_markdown", "") or \
                       getattr(md_obj, "raw_markdown", "") or ""
            if not body:
                body = getattr(r, "cleaned_html", "") or ""

            if len(body.encode("utf-8")) < min_ok_bytes:
                failed.append(url)
                await progress.record_url(
                    url, status="extract_empty", tier="playwright",
                    extracted_chars=len(body),
                    error_msg=f"body too short ({len(body)}B)",
                )
                continue

            slug = _slugify(urlparse(url).path or framework_slug)
            await store.add_page(
                slug=slug, url=url, body=body,
                tier="playwright", title=slug,
            )
            written += 1
            await progress.record_url(
                url, status="success", tier="playwright",
                extracted_chars=len(body),
            )
            await progress.update(current=written, last_url=url)

    await progress.start(tier="playwright", total=len(urls))

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        await _install_resource_blocker(crawler)

        # PRIMARY PASS — per-URL session_id isolates each page's context.
        if hasattr(primary_cfg, "clone"):
            per_url_configs = [
                primary_cfg.clone(session_id=f"crawl-{uuid.uuid4().hex[:12]}")
                for _ in urls
            ]
            stream = await crawler.arun_many(
                urls, config=per_url_configs, dispatcher=dispatcher_primary,
            )
        else:
            stream = await crawler.arun_many(
                urls, config=primary_cfg, dispatcher=dispatcher_primary,
            )
        await _consume(stream, "primary")

        # RETRY PASS — patient timeouts for ERR_ABORTED / nav-timeout class.
        if transient_nav_failures:
            logger.info(
                f"[playwright] retry pass: {len(transient_nav_failures)} URLs "
                f"with longer timeouts"
            )
            await asyncio.sleep(_RETRY_DELAY_S)
            if hasattr(retry_cfg, "clone"):
                per_url_retry = [
                    retry_cfg.clone(session_id=f"retry-{uuid.uuid4().hex[:12]}")
                    for _ in transient_nav_failures
                ]
                stream = await crawler.arun_many(
                    transient_nav_failures,
                    config=per_url_retry, dispatcher=dispatcher_retry,
                )
            else:
                stream = await crawler.arun_many(
                    transient_nav_failures,
                    config=retry_cfg, dispatcher=dispatcher_retry,
                )
            await _consume(stream, "retry")

    if written == 0:
        await progress.finish(status="failed")
    else:
        await progress.finish(status="done")
    return written, failed
