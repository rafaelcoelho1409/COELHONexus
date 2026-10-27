"""Fetch sitemap.xml (flat or index, recursively flattened), apply doc-page filter (drop blog/news/marketing/assets), and fetch all matching pages. Progress becomes determinate after parse."""
from __future__ import annotations
import domains
from . import domain, params

import asyncio
import logging
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


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
    try:
        soup = BeautifulSoup(resp.text or "", "lxml-xml")
    except Exception:
        soup = BeautifulSoup(resp.text or "", "html.parser")
    out: list[tuple[str, str]] = []
    for sm in soup.find_all("sitemap"):
        loc = sm.find("loc")
        if loc and loc.text:
            nested = await _expand_sitemap(client, loc.text.strip(), depth + 1)
            out.extend(nested)
    for u in soup.find_all("url"):
        loc = u.find("loc")
        if loc and loc.text:
            lm = u.find("lastmod")
            out.append((
                loc.text.strip(),
                (lm.text.strip() if lm and lm.text else "")[:32],
            ))
    return out


async def scoped_urls(client: httpx.AsyncClient, url: str, path_filter: dict | None = None) -> list[str]:
    """The page URLs a Tier 3 run of this sitemap would fetch (before any language filter), in sitemap order, each once.
    `[]` when the sitemap cannot be read. Lets another tier compare its own list of pages with the site's."""
    seen: set[str] = set()
    urls: list[str] = []
    for page_url, _lastmod in await _expand_sitemap(client, url):
        if page_url not in seen and domain.in_scope(page_url, path_filter):
            seen.add(page_url)
            urls.append(page_url)
    return urls


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
    if (
        resp.status_code != 200
        or len(text.encode("utf-8")) < params.MIN_OK_BYTES
        or domains.dd.ingestion.tiers.tier1.domain.looks_like_html(text)
    ):
        return None
    return text, int((time.monotonic() - t0) * 1000)


async def site_serves_twins(
    client: httpx.AsyncClient,
    urls: list[str],
    pacer: domains.dd.ingestion.tiers.fetch.service.Pacer | None = None,
) -> bool:
    """Whether the site publishes a markdown twin of its pages: TWIN_PROBE_SAMPLE pages spread over `urls` are asked for
    theirs, and at least TWIN_PROBE_MIN_OK must have one (see `_fetch_twin`: a 200 that is really an HTML page is not a twin)."""
    sample_size = min(params.TWIN_PROBE_SAMPLE, len(urls))
    if sample_size < params.TWIN_PROBE_MIN_OK:
        return False
    step = len(urls) / sample_size
    sample = [urls[int(i * step)] for i in range(sample_size)]
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
) -> tuple[str, str, str, str] | None:
    if twin:
        twin_page = await _fetch_twin(client, url, pacer)
        if twin_page is not None:
            markdown, twin_ms = twin_page
            title = domains.dd.ingestion.post.domain.display_title(markdown, "")
            await progress.record_url(
                url,
                status = "success",
                tier = "sitemap",
                http_code = 200,
                fetch_ms = twin_ms,
                bytes_fetched = len(markdown.encode("utf-8")),
                extracted_chars = len(markdown),
            )
            slug = domain.slugify(title or urlparse(url).path)
            return (slug, url, markdown, title or slug)
    t0 = time.monotonic()
    try:
        resp = await _get(client, url, pacer)
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
    body = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(raw, source_url = url)
    title = domains.dd.ingestion.tiers.extract.domain.extract_title(raw)
    if len(body.encode("utf-8")) < params.MIN_OK_BYTES:
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
    slug = domain.slugify(title or urlparse(url).path)
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
) -> int:
    logger.info(f"[tier-3] framework={framework_slug} sitemap={url}")
    await progress.start(tier = "sitemap", total = 0)
    allow, deny = domains.dd.ingestion.filters.domain.build_language_filter(language)
    polyglot = domains.dd.ingestion.filters.domain.is_polyglot(framework_name or "")

    def _keep(u: str) -> bool:
        if not domain.in_scope(u, path_filter):
            return False
        if polyglot and language:
            return domains.dd.ingestion.filters.domain.should_keep(u, allow, deny)
        if allow or deny:
            return domains.dd.ingestion.filters.domain.should_keep(u, allow, deny)
        return True

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(params.TIMEOUT_S, connect = 10.0),
        follow_redirects = True,
    ) as client:
        all_entries = await _expand_sitemap(client, url)
        if not all_entries:
            # `downgrade`, not `failed`: a lower tier (the docs crawl, the repository) may still serve this framework.
            await progress.finish(status = "downgrade")
            raise domains.dd.ingestion.tiers.errors.TierUnavailable(f"Tier 3: {url} yielded zero URLs")
        all_urls = [u for u, _ in all_entries]
        lastmod_by_url = {u: lm for u, lm in all_entries if lm}
        kept = [u for u in all_urls if _keep(u)]
        seen: set[str] = set()
        deduped: list[str] = []
        for u in kept:
            if u in seen:
                continue
            seen.add(u)
            deduped.append(u)
        n_lastmod = sum(1 for u in deduped if lastmod_by_url.get(u))
        logger.info(
            f"[tier-3] {len(all_urls)} total → {len(kept)} after filter → "
            f"{len(deduped)} after dedup ({n_lastmod} with lastmod)"
        )
        if not deduped:
            await progress.finish(status = "downgrade")
            raise domains.dd.ingestion.tiers.errors.TierUnavailable(
                f"Tier 3: {url} lists {len(all_urls)} URLs, none inside the catalog's path_filter"
            )
        await progress.update_total(len(deduped))
        host = (urlparse(url).netloc or "").lower()

        async def _announce_wait(seconds: float) -> None:
            logger.info(f"[tier-3] {framework_slug}: {host} asked for a pause, waiting {seconds:.0f}s")
            await progress.record_url(
                host, status = "rate_limited", tier = "sitemap",
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
            """Collision-free slug within this run (ported from Tier 2:
            first claimant keeps the bare slug, later same-title pages take
            the nearest distinctive URL ancestor, counter as fallback).
            Keys were already unique (idx-prefixed) — this only cleans
            manifests/explorer titles. Cross-product same-topic pages
            (langchain/langgraph/deepagents `streaming`×3) and tutorial-vs-
            reference pairs (fastapi `middleware`×2) are the live cases."""
            async with slug_lock:
                if base not in slug_owner:
                    slug_owner[base] = link
                    return base
                if slug_owner[base] == link:
                    return base
                stem = domain.collision_suffix(base, link) or "x"
                cand = domain.suffixed_slug(base, stem)
                i = 2
                while cand in slug_owner and slug_owner[cand] != link:
                    cand = domain.suffixed_slug(base, f"{stem}-{i}")
                    i += 1
                slug_owner[cand] = link
                return cand

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
                )
            if r is not None:
                slug, src_url, body, title = r
                slug = await _claim_slug(slug, src_url)
                await store.add_page(
                    slug = slug,
                    url = src_url,
                    body = body,
                    tier = "sitemap",
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
