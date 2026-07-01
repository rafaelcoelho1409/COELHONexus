"""Fetch sitemap.xml (flat or index, recursively flattened), apply doc-page filter (drop blog/news/marketing/assets), and fetch all matching pages. Progress becomes determinate after parse."""
import asyncio
import logging
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

from ...artifacts import extract_and_save_artifacts
from ...filters import (
    NON_TARGET_LANGUAGE_PATH_RE,
    build_language_filter,
    is_polyglot,
    passes_path_filter,
    should_keep,
)
from ...progress import Progress
from ...storage import Store
from ..extract import extract_title, html_to_markdown
from .domain import slugify
from .params import CONCURRENCY, INDEX_MAX_DEPTH, MIN_OK_BYTES, TIMEOUT_S, USER_AGENT


logger = logging.getLogger(__name__)


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 8),
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": USER_AGENT})


async def _expand_sitemap(
    client: httpx.AsyncClient,
    url: str,
    depth: int = 0,
) -> list[str]:
    """Recursively flatten sitemap indexes. Returns a list of page URLs."""
    if depth > INDEX_MAX_DEPTH:
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
    for sm in soup.find_all("sitemap"):
        loc = sm.find("loc")
        if loc and loc.text:
            nested = await _expand_sitemap(client, loc.text.strip(), depth + 1)
            out.extend(nested)
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
    framework_slug: str | None = None,
    store: Store | None = None,
) -> tuple[str, str, str, str] | None:
    t0 = time.monotonic()
    try:
        resp = await _get(client, url)
    except Exception as e:
        await progress.record_url(
            url,
            status = "fetch_error",
            tier = "sitemap",
            fetch_ms = int((time.monotonic() - t0) * 1000),
            error_msg = f"{type(e).__name__}: {e}",
        )
        return None
    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            url,
            status = "http_error",
            tier = "sitemap",
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(resp.content or b""),
            error_msg = f"HTTP {resp.status_code}",
        )
        return None
    raw = resp.text or ""
    if framework_slug and store is not None:
        try:
            raw, n_art = await extract_and_save_artifacts(
                raw,
                url,
                slug = framework_slug,
                store = store,
                client = client,
            )
            if n_art:
                logger.info(
                    f"[tier-3] {url}: saved {n_art} artifact(s) "
                    f"to ingestion/{framework_slug}/artifacts/"
                )
        except Exception as e:
            logger.warning(
                f"[tier-3] artifact extraction failed for {url}: "
                f"{type(e).__name__}: {e}"
            )
    body = html_to_markdown(raw, source_url = url)
    title = extract_title(raw)
    if len(body.encode("utf-8")) < MIN_OK_BYTES:
        await progress.record_url(
            url,
            status = "extract_empty",
            tier = "sitemap",
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(raw),
            extracted_chars = len(body),
            error_msg = "extracted body too short",
        )
        return None
    await progress.record_url(
        url,
        status = "success",
        tier = "sitemap",
        http_code = resp.status_code,
        fetch_ms = fetch_ms,
        bytes_fetched = len(raw),
        extracted_chars = len(body),
    )
    slug = slugify(title or urlparse(url).path)
    return (slug, url, body, title or slug)


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: Progress,
    store: Store,
    language: str | None = None,
    framework_name: str | None = None,
    path_filter: dict | None = None,
) -> int:
    logger.info(f"[tier-3] framework={framework_slug} sitemap={url}")
    await progress.start(tier = "sitemap", total = 0)
    allow, deny = build_language_filter(language)
    polyglot = is_polyglot(framework_name or "")

    def _keep(u: str) -> bool:
        p = urlparse(u)
        if NON_TARGET_LANGUAGE_PATH_RE.search(p.path or ""):
            return False
        if not passes_path_filter(u, path_filter):
            return False
        if polyglot and language:
            return should_keep(u, allow, deny)
        if allow or deny:
            return should_keep(u, allow, deny)
        return True

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(TIMEOUT_S, connect = 10.0),
        follow_redirects = True,
    ) as client:
        all_urls = await _expand_sitemap(client, url)
        if not all_urls:
            await progress.finish(status = "failed")
            raise RuntimeError(f"Tier 3: {url} yielded zero URLs")
        kept = [u for u in all_urls if _keep(u)]
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
        sem = asyncio.Semaphore(CONCURRENCY)
        written = 0

        async def _bound(u: str):
            nonlocal written
            async with sem:
                await progress.raise_if_cancelled()
                r = await _fetch_page(
                    client,
                    u,
                    progress = progress,
                    framework_slug = framework_slug,
                    store = store,
                )
            if r is not None:
                slug, src_url, body, title = r
                await store.add_page(
                    slug = slug,
                    url = src_url,
                    body = body,
                    tier = "sitemap",
                    title = title,
                )
                written += 1
            await progress.update(current = written, last_url = u)
            return r
        await asyncio.gather(
            *(_bound(u) for u in deduped),
            return_exceptions = False,
        )
    if written == 0:
        await progress.finish(status = "failed")
        raise RuntimeError(f"Tier 3: {url} all pages failed")
    store.reorder_by_url_list(deduped)
    await progress.finish(status = "done")
    return written
