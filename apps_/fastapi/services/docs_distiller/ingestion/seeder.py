"""Crawl4AI AsyncUrlSeeder — Phase 1 URL discovery for Tier 4.

Uses the seeder's `sitemap+cc` source (sitemap.xml + Common Crawl index)
to discover candidate URLs for a host, scoped to the docs subtree when a
meaningful path is given. Falls back to plain httpx BFS when the seeder
returns nothing (handled in tier4_http).

Imports are deferred so the heavy crawl4ai module only loads when the
seeder is actually invoked (cold-start friendly for tiers 1/2/3 which
never need it).
"""
import logging
from typing import Optional


logger = logging.getLogger(__name__)


def _seed_pattern_for(path: str) -> Optional[str]:
    """Convert a docs subtree path to the seeder's glob pattern. Bare
    host / root path → None (full domain search)."""
    cleaned = (path or "").rstrip("/")
    if not cleaned or cleaned in ("/", ""):
        return None
    return f"*{cleaned}*"


async def discover_urls(
    host: str,
    docs_path: str,
    *,
    max_urls: int = 10_000_000,
) -> list[str]:
    """Return a list of `valid`/`found` URLs from the seeder. Empty list on
    any failure — caller should fall back to httpx BFS."""
    try:
        from crawl4ai import AsyncUrlSeeder, SeedingConfig
    except ImportError as e:
        logger.warning(f"[seeder] crawl4ai not installed: {e}")
        return []

    cfg = SeedingConfig(
        source="sitemap+cc",
        pattern=_seed_pattern_for(docs_path),
        max_urls=max_urls,
        extract_head=False,
    )
    try:
        async with AsyncUrlSeeder() as seeder:
            results = await seeder.urls(host, cfg)
    except Exception as e:
        logger.warning(f"[seeder] {host} discovery failed: {e}")
        return []

    out = [
        d.get("url") for d in (results or [])
        if d.get("url") and d.get("status") in ("valid", "found")
    ]
    logger.info(
        f"[seeder] {host} found {len(out)} URLs "
        f"(pattern={cfg.pattern!r})"
    )
    return out
