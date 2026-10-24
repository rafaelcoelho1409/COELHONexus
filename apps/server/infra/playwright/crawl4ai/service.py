"""Crawl4AI building blocks bound to the Playwright browser service.

The only module that imports `crawl4ai`. `build_browser_config()` points Crawl4AI at the
headless CDP sidecar (or a local Chromium); the Crawl4AI classes are re-exported so
domains never import `crawl4ai` themselves."""
from __future__ import annotations
import infra
from . import params

import logging

from crawl4ai import (
    AsyncUrlSeeder,
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    LXMLWebScrapingStrategy,
    SeedingConfig,
)
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher, RateLimiter
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator


logger = logging.getLogger(__name__)


def build_browser_config() -> BrowserConfig:
    """BrowserConfig using the remote headless CDP browser if it resolves, else a local Chromium."""
    cdp_headless = infra.playwright.params.CDP_HEADLESS
    cdp_ws = infra.playwright.service.resolve_ws_url(cdp_headless, cache = True) if cdp_headless else None
    if cdp_ws:
        logger.info(f"[playwright] using remote CDP {cdp_ws[:80]}…")
        return BrowserConfig(
            browser_type = "chromium",
            use_managed_browser = True,
            cdp_url = cdp_ws,
            headless = True,
            verbose = False,
        )
    logger.info("[playwright] CDP unresolved — falling back to local Chromium")
    return BrowserConfig(
        browser_type = "chromium",
        headless = True,
        verbose = False,
        proxy = params.PROXY_URL or None,
    )


# Public surface for the rest of the code (the re-exports are intentional).
__all__ = [
    "AsyncUrlSeeder", 
    "AsyncWebCrawler", 
    "BrowserConfig", 
    "CacheMode", 
    "CrawlerRunConfig",
    "DefaultMarkdownGenerator", 
    "LXMLWebScrapingStrategy", 
    "MemoryAdaptiveDispatcher",
    "PruningContentFilter", 
    "RateLimiter", 
    "SeedingConfig", 
    "build_browser_config",
]
