"""Tier 3 — fetch a `sitemap.xml`, then each URL it lists.

Supports both flat sitemaps (a list of `<url><loc>…</loc></url>`) and
sitemap indexes (a list of `<sitemap><loc>…</loc></sitemap>` pointing to
nested sitemaps). Recursively flattens to a single URL set.

Applies a conservative doc-page filter (drop blog, news, marketing, legal,
non-HTML assets) and a hard cap to keep the run bounded. Total page count
becomes known after parse → indeterminate → determinate progress switch.
"""
import asyncio
import logging
import re
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .extract import extract_title, html_to_markdown
from .filters import (
    NON_TARGET_LANGUAGE_PATH_RE,
    build_language_filter,
    is_polyglot,
    should_keep,
)
from .progress import Progress
from .store import Store


logger = logging.getLogger(__name__)

_USER_AGENT = "COELHONexus-DocsDistiller-Tier3/1.0"
_TIMEOUT_S = 30.0
_CONCURRENCY = 8
_MIN_OK_BYTES = 200
# Cap removed (2026-05-17) — was 600, silently truncating large docs sites
# like Docker (1512 URLs after filter → only 600 kept). Sitemap is bounded
# by construction (finite list); concurrency + per-URL timeout already
# bound wall-time. If a 50k-URL sitemap shows up, raise concurrency or
# add a per-framework override; don't truncate by default.
# Nested sitemap depth limit (sitemap index → sitemap → …).
_INDEX_MAX_DEPTH = 3


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


@retry(
    reraise=True,
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=8),
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers={"User-Agent": _USER_AGENT})


async def _expand_sitemap(
    client: httpx.AsyncClient,
    url: str,
    depth: int = 0,
) -> list[str]:
    """Recursively flatten sitemap indexes. Returns a list of page URLs."""
    if depth > _INDEX_MAX_DEPTH:
        logger.info(f"[tier-3] sitemap depth cap hit at {url}")
        return []
    try:
        resp = await _get(client, url)
    except Exception as e:
        logger.info(f"[tier-3] sitemap fetch failed for {url}: {e}")
        return []
    if resp.status_code != 200:
        logger.info(f"[tier-3] sitemap {url} → HTTP {resp.status_code}")
        return []

    try:
        soup = BeautifulSoup(resp.text or "", "lxml-xml")
    except Exception:
        soup = BeautifulSoup(resp.text or "", "html.parser")

    out: list[str] = []
    # Sitemap index — recurse
    for sm in soup.find_all("sitemap"):
        loc = sm.find("loc")
        if loc and loc.text:
            nested = await _expand_sitemap(client, loc.text.strip(), depth + 1)
            out.extend(nested)

    # Regular URLs
    for u in soup.find_all("url"):
        loc = u.find("loc")
        if loc and loc.text:
            out.append(loc.text.strip())

    return out


async def _fetch_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    progress: Progress,
) -> tuple[str, str, str, str] | None:
    t0 = time.monotonic()
    try:
        resp = await _get(client, url)
    except Exception as e:
        await progress.record_url(
            url, status="fetch_error", tier="sitemap",
            fetch_ms=int((time.monotonic() - t0) * 1000),
            error_msg=f"{type(e).__name__}: {e}",
        )
        return None

    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            url, status="http_error", tier="sitemap",
            http_code=resp.status_code, fetch_ms=fetch_ms,
            bytes_fetched=len(resp.content or b""),
            error_msg=f"HTTP {resp.status_code}",
        )
        return None

    raw = resp.text or ""
    body = html_to_markdown(raw, source_url=url)
    title = extract_title(raw)

    if len(body.encode("utf-8")) < _MIN_OK_BYTES:
        await progress.record_url(
            url, status="extract_empty", tier="sitemap",
            http_code=resp.status_code, fetch_ms=fetch_ms,
            bytes_fetched=len(raw), extracted_chars=len(body),
            error_msg="extracted body too short",
        )
        return None

    await progress.record_url(
        url, status="success", tier="sitemap",
        http_code=resp.status_code, fetch_ms=fetch_ms,
        bytes_fetched=len(raw), extracted_chars=len(body),
    )
    slug = _slugify(title or urlparse(url).path)
    return (slug, url, body, title or slug)


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: Progress,
    store: Store,
    language: str | None = None,
    framework_name: str | None = None,
) -> int:
    logger.info(f"[tier-3] framework={framework_slug} sitemap={url}")
    await progress.start(tier="sitemap", total=0)

    allow, deny = build_language_filter(language)
    polyglot = is_polyglot(framework_name or "")

    def _keep(u: str) -> bool:
        p = urlparse(u)
        if NON_TARGET_LANGUAGE_PATH_RE.search(p.path or ""):
            return False
        if polyglot and language:
            return should_keep(u, allow, deny)
        if allow or deny:
            return should_keep(u, allow, deny)
        return True

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(_TIMEOUT_S, connect=10.0),
        follow_redirects=True,
    ) as client:
        all_urls = await _expand_sitemap(client, url)
        if not all_urls:
            await progress.finish(status="failed")
            raise RuntimeError(f"Tier 3: {url} yielded zero URLs")

        kept = [u for u in all_urls if _keep(u)]
        # Dedup while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for u in kept:
            if u in seen:
                continue
            seen.add(u)
            deduped.append(u)

        logger.info(
            f"[tier-3] {len(all_urls)} total → {len(kept)} after filter → "
            f"{len(deduped)} after dedup"
        )
        await progress.update_total(len(deduped))

        sem = asyncio.Semaphore(_CONCURRENCY)
        # Stream each page to MinIO as soon as its fetch returns. No
        # post-gather serial write loop — that pattern held all bodies in
        # RAM and serialized writes, doubling wall-time on big corpora
        # like Docker (1500+ pages). Now: fetch + write + manifest-append
        # all overlap; partial state is persisted on crash; progress bar
        # reflects actual MinIO commits.
        written = 0

        async def _bound(u: str):
            nonlocal written
            async with sem:
                await progress.raise_if_cancelled()
                r = await _fetch_page(client, u, progress=progress)
            if r is not None:
                slug, src_url, body, title = r
                await store.add_page(
                    slug=slug, url=src_url, body=body,
                    tier="sitemap", title=title,
                )
                written += 1
            # Update fires per fetch (incl. failed fetches counted in
            # progress.total but not in `written`) — keeps the bar moving.
            await progress.update(current=written, last_url=u)
            return r

        await asyncio.gather(
            *(_bound(u) for u in deduped),
            return_exceptions=False,
        )

    if written == 0:
        await progress.finish(status="failed")
        raise RuntimeError(f"Tier 3: {url} all pages failed")

    await progress.finish(status="done")
    return written
