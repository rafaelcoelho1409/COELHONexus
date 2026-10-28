"""Fetch sitemap.xml (flat or index, recursively flattened), apply doc-page filter (drop blog/news/marketing/assets), and fetch all matching pages. Progress becomes determinate after parse."""
from __future__ import annotations
import domains
from . import domain, params

import asyncio
import logging
import time

import httpx


logger = logging.getLogger(__name__)


async def _get(
    client: httpx.AsyncClient,
    url: str,
    pacer: domains.dd.ingestion.tiers.fetch.service.Pacer | None = None,
) -> httpx.Response:
    """GET with the shared crawl policy: retries on timeouts, network errors and 429/5xx, a site's Retry-After and
    rate-limit headers honoured, and — given the crawl's `pacer` — all concurrent requests waiting together."""
    fetch = domains.dd.ingestion.tiers.fetch
    return await fetch.service.get(
        client,
        url,
        headers = {"User-Agent": params.USER_AGENT},
        attempts = fetch.params.CRAWL_ATTEMPTS,
        wait_initial = fetch.params.CRAWL_WAIT_INITIAL_S,
        wait_max = fetch.params.CRAWL_WAIT_MAX_S,
        retry_after_cap = fetch.params.CRAWL_RETRY_AFTER_MAX_S,
        pacer = pacer,
    )


async def _expand_sitemap(
    client: httpx.AsyncClient,
    url: str,
    depth: int = 0,
) -> list[tuple[str, str]]:
    """Recursively flatten sitemap indexes. Returns [(page_url, lastmod)] —
    `lastmod` is "" when the index carries none. Callers must treat "" as
    unknown, never as old."""
    if depth > params.INDEX_MAX_DEPTH:
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
    nested_sitemaps, pages = domain.parse_sitemap(resp.text or "")
    out: list[tuple[str, str]] = []
    for nested_url in nested_sitemaps:
        out.extend(await _expand_sitemap(client, nested_url, depth + 1))
    out.extend(pages)
    return out


async def scoped_urls(client: httpx.AsyncClient, url: str, path_filter: dict | None = None) -> list[str]:
    """The page URLs a Tier 3 run of this sitemap would fetch (before any language filter), in sitemap order, each once.
    `[]` when the sitemap cannot be read. Lets another tier compare its own list of pages with the site's."""
    return domain.unique_in_order(
        [page_url for page_url, _lastmod in await _expand_sitemap(client, url) if domain.in_scope(page_url, path_filter)]
    )


async def _fetch_twin(
    client: httpx.AsyncClient,
    url: str,
    pacer: domains.dd.ingestion.tiers.fetch.service.Pacer | None,
) -> tuple[str, int] | None:
    """The page's markdown twin, on a site that publishes one (see `domain.markdown_twin_url`): `(markdown, fetch_ms)`,
    or None — the HTML is scraped instead. A twin that is missing, too short, or really an HTML page (a site that answers
    200 to anything) is no twin."""
    twin = domain.markdown_twin_url(url)
    if twin is None:
        return None
    t0 = time.monotonic()
    try:
        resp = await _get(client, twin, pacer)
    except Exception:  # noqa: BLE001
        return None
    text = resp.text or ""
    if not domain.usable_twin(resp.status_code, text):
        return None
    return text, int((time.monotonic() - t0) * 1000)


async def site_serves_twins(
    client: httpx.AsyncClient,
    urls: list[str],
    pacer: domains.dd.ingestion.tiers.fetch.service.Pacer | None = None,
) -> bool:
    """Whether the site publishes a markdown twin of its pages: TWIN_PROBE_SAMPLE pages spread over `urls` are asked for
    theirs, and at least TWIN_PROBE_MIN_OK must have one (see `_fetch_twin`: a 200 that is really an HTML page is not a twin)."""
    sample = domain.twin_sample(urls)
    if not sample:
        return False
    found = await asyncio.gather(*(_fetch_twin(client, u, pacer) for u in sample))
    return sum(1 for twin in found if twin is not None) >= params.TWIN_PROBE_MIN_OK


async def _fetch_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    framework_slug: str | None = None,
    store: domains.dd.ingestion.storage.service.Store | None = None,
    twin: bool = False,
    pacer: domains.dd.ingestion.tiers.fetch.service.Pacer | None = None,
    code_language: str = "",
) -> tuple[str, str, str, str] | None:
    if twin:
        twin_page = await _fetch_twin(client, url, pacer)
        if twin_page is not None:
            markdown, twin_ms = twin_page
            title = domains.dd.ingestion.post.domain.display_title(markdown, "")
            await progress.record_url(
                url,
                status = "success",
                tier = params.TIER_KIND,
                http_code = 200,
                fetch_ms = twin_ms,
                bytes_fetched = len(markdown.encode("utf-8")),
                extracted_chars = len(markdown),
            )
            slug = domain.page_slug(title, url)
            return (slug, url, markdown, title or slug)
    t0 = time.monotonic()
    try:
        resp = await _get(client, url, pacer)
    except Exception as e:
        await progress.record_url(
            url,
            status = "fetch_error",
            tier = params.TIER_KIND,
            fetch_ms = int((time.monotonic() - t0) * 1000),
            error_msg = f"{type(e).__name__}: {e}",
        )
        return None
    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            url,
            status = "http_error",
            tier = params.TIER_KIND,
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(resp.content or b""),
            error_msg = f"HTTP {resp.status_code}",
        )
        return None
    raw = resp.text or ""
    if framework_slug and store is not None:
        try:
            raw, n_art = await domains.dd.ingestion.artifacts.service.extract_and_save_artifacts(
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
    body = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(raw, source_url = url, default_code_language = code_language)
    title = domains.dd.ingestion.tiers.extract.domain.extract_title(raw)
    if len(body.encode("utf-8")) < params.MIN_OK_BYTES:
        await progress.record_url(
            url,
            status = "extract_empty",
            tier = params.TIER_KIND,
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
        tier = params.TIER_KIND,
        http_code = resp.status_code,
        fetch_ms = fetch_ms,
        bytes_fetched = len(raw),
        extracted_chars = len(body),
    )
    slug = domain.page_slug(title, url)
    return (slug, url, body, title or slug)


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    store: domains.dd.ingestion.storage.service.Store,
    language: str | None = None,
    framework_name: str | None = None,
    path_filter: dict | None = None,
    code_language: str | None = None,
) -> int:
    logger.info(f"[tier-3] framework={framework_slug} sitemap={url}")
    await progress.start(tier = params.TIER_KIND, total = 0)
    allow, deny = domains.dd.ingestion.filters.domain.build_language_filter(language)
    polyglot = domains.dd.ingestion.filters.domain.is_polyglot(framework_name or "")

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(params.TIMEOUT_S, connect = params.CONNECT_TIMEOUT_S),
        follow_redirects = True,
    ) as client:
        all_entries = await _expand_sitemap(client, url)
        if not all_entries:
            # `downgrade`, not `failed`: a lower tier (the docs crawl, the repository) may still serve this framework.
            await progress.finish(status = "downgrade")
            raise domains.dd.ingestion.tiers.errors.TierUnavailable(f"Tier 3: {url} yielded zero URLs")
        all_urls = [u for u, _ in all_entries]
        lastmod_by_url = {u: lm for u, lm in all_entries if lm}
        kept = [
            u for u in all_urls
            if domain.wanted(u, path_filter, polyglot = polyglot, language = language, allow = allow, deny = deny)
        ]
        deduped = domain.unique_in_order(kept)
        n_distinct = len(deduped)
        deduped = domain.collapse_versions(deduped)
        n_lastmod = sum(1 for u in deduped if lastmod_by_url.get(u))
        logger.info(
            f"[tier-3] {len(all_urls)} total → {len(kept)} after filter → "
            f"{n_distinct} after dedup → {len(deduped)} after keeping one release of a page ({n_lastmod} with lastmod)"
        )
        if not deduped:
            await progress.finish(status = "downgrade")
            raise domains.dd.ingestion.tiers.errors.TierUnavailable(
                f"Tier 3: {url} lists {len(all_urls)} URLs, none inside the catalog's path_filter"
            )
        await progress.update_total(len(deduped))
        host = domains.dd.ingestion.tiers.fetch.domain.host_of(url)

        async def _announce_wait(seconds: float) -> None:
            logger.info(f"[tier-3] {framework_slug}: {host} asked for a pause, waiting {seconds:.0f}s")
            await progress.record_url(
                host, status = "rate_limited", tier = params.TIER_KIND,
                error_msg = f"the site asked for a pause: waiting {seconds:.0f}s before going on",
            )

        pacer = domains.dd.ingestion.tiers.fetch.service.Pacer(on_wait = _announce_wait)
        twins = bool(getattr(store, "markdown_twins", False))
        if twins:
            logger.info(f"[tier-3] {framework_slug}: the site's index served markdown pages — asking for each page's markdown twin first")
        elif await site_serves_twins(client, deduped, pacer):
            twins = True
            logger.info(f"[tier-3] {framework_slug}: the site publishes a markdown twin of its pages — asking for each page's twin first")
        sem = asyncio.Semaphore(params.CONCURRENCY)
        slug_lock = asyncio.Lock()
        slug_owner: dict[str, str] = {}
        written = 0

        async def _claim_slug(base: str, link: str) -> str:
            """A collision-free slug within this run (`domain.free_slug`): the first page to want a slug keeps it."""
            async with slug_lock:
                slug = domain.free_slug(slug_owner, base, link)
                slug_owner[slug] = link
                return slug

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
                    twin = twins,
                    pacer = pacer,
                    code_language = code_language or "",
                )
            if r is not None:
                slug, src_url, body, title = r
                slug = await _claim_slug(slug, src_url)
                await store.add_page(
                    slug = slug,
                    url = src_url,
                    body = body,
                    tier = params.TIER_KIND,
                    title = title,
                    lastmod = lastmod_by_url.get(src_url, ""),
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
    # No `finish`: post-processing and finalizing follow, and the dispatcher alone marks the run done.
    return written
