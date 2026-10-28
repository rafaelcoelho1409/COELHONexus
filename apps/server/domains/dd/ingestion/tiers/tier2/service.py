"""Fetch llms.txt index (AnswerDotAI spec), then all linked pages concurrently. Markdown responses pass through; HTML goes through the extractor. Progress becomes determinate after index parse."""
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


async def _site_pages(client: httpx.AsyncClient, sitemap_url: str, path_filter: dict | None) -> int | None:
    """How many pages the site's sitemap has (the ones a Tier 3 run would fetch), or None when it cannot be told —
    a missing or slow sitemap is no reason to doubt the index."""
    try:
        urls = await asyncio.wait_for(
            domains.dd.ingestion.tiers.tier3.service.scoped_urls(client, sitemap_url, path_filter),
            timeout = params.COVERAGE_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001
        logger.info(f"[tier-2] no page count from {sitemap_url}: {type(e).__name__}: {e}")
        return None
    return len(urls) or None


async def _fetch_one(
    client: httpx.AsyncClient,
    title: str,
    url: str,
    *,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    tier_name: str,
    framework_slug: str | None = None,
    store: domains.dd.ingestion.storage.service.Store | None = None,
    index_host: str = "",
    pacer: domains.dd.ingestion.tiers.fetch.service.Pacer | None = None,
    code_language: str = "",
) -> tuple[str, str, str, str] | None:
    """Returns (slug, url, body_markdown, title) on success, None on failure.
    Records progress + URL log internally."""
    t0 = time.monotonic()
    try:
        resp = await _get(client, url, pacer)
    except Exception as e:
        await progress.record_url(
            url,
            status = "fetch_error",
            tier = tier_name,
            fetch_ms = int((time.monotonic() - t0) * 1000),
            error_msg = f"{type(e).__name__}: {e}",
        )
        return None
    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            url,
            status = "http_error",
            tier = tier_name,
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(resp.content or b""),
            error_msg = f"HTTP {resp.status_code}",
        )
        return None
    raw = resp.text or ""
    cross_host = bool(index_host) and domains.dd.ingestion.tiers.fetch.domain.host_of(url) != index_host
    kind = domain.body_kind(resp)
    if kind == "markdown":
        body_md = raw
        if cross_host:
            # Gist/raw mirrors prepend the filename as a debris H1 (observed:
            # `starlette-sml.md` starts with a literal `# index.md` line) —
            # strip it so it never seeds a junk heading downstream.
            body_md = domain.strip_filename_h1(body_md, url)
    elif kind == "text":
        body_md = raw
    elif kind == "python":
        body_md = domain.fence_python(raw)
    else:
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
                        f"[tier-2] {url}: saved {n_art} artifact(s) "
                        f"to ingestion/{framework_slug}/artifacts/"
                    )
            except Exception as e:
                logger.warning(
                    f"[tier-2] artifact extraction failed for {url}: "
                    f"{type(e).__name__}: {e}"
                )
        body_md = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(raw, source_url = url, default_code_language = code_language)
        if not title:
            title = domains.dd.ingestion.tiers.extract.domain.extract_title(raw) or title
    if len(body_md.encode("utf-8")) < params.MIN_OK_BYTES:
        await progress.record_url(
            url,
            status = "extract_empty",
            tier = tier_name,
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(raw),
            extracted_chars = len(body_md),
            error_msg = "extracted body too short",
        )
        return None
    await progress.record_url(
        url,
        status = "success",
        tier = tier_name,
        http_code = resp.status_code,
        fetch_ms = fetch_ms,
        bytes_fetched = len(raw),
        extracted_chars = len(body_md),
    )
    slug = domain.page_slug(title, url)
    return (slug, url, body_md, title or slug)


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    store: domains.dd.ingestion.storage.service.Store,
    path_filter: dict | None = None,
    sitemap_url: str | None = None,
    code_language: str | None = None,
) -> int:
    """Fetch index, fan out to N concurrent page fetches, write each to
    store. Returns the number of pages written. Raises TierUnavailable if the
    index itself can't be fetched (404, error, a filter that leaves nothing)
    or names far fewer pages than the site's sitemap (`sitemap_url`, the
    catalog's: a curated agent index is not the docs), so the dispatcher
    moves on to the next declared source. `path_filter` (the catalog's
    `include`/`exclude`) trims an index that lists more than the framework."""
    logger.info(f"[tier-2] framework={framework_slug} index={url}")
    await progress.start(tier = params.TIER_KIND, total = 0)
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(params.TIMEOUT_S, connect = params.CONNECT_TIMEOUT_S),
        follow_redirects = True,
    ) as client:
        t0 = time.monotonic()
        try:
            resp = await _get(client, url)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            await progress.record_url(
                url,
                status = "fetch_error",
                tier = params.TIER_KIND,
                fetch_ms = int((time.monotonic() - t0) * 1000),
                error_msg = err,
            )
            # `downgrade`, not `failed`: the run is not over — a lower tier may still serve this framework.
            await progress.finish(status = "downgrade")
            raise domains.dd.ingestion.tiers.errors.TierUnavailable(f"Tier 2 index fetch failed for {url}: {err}")
        if resp.status_code != 200:
            await progress.record_url(
                url,
                status = "http_error",
                tier = params.TIER_KIND,
                http_code = resp.status_code,
                fetch_ms = int((time.monotonic() - t0) * 1000),
                error_msg = f"HTTP {resp.status_code}",
            )
            await progress.finish(status = "downgrade")
            raise domains.dd.ingestion.tiers.errors.TierUnavailable(f"Tier 2: {url} → HTTP {resp.status_code}")
        await progress.record_url(
            url,
            status = "success",
            tier = params.TIER_KIND,
            http_code = resp.status_code,
            fetch_ms = int((time.monotonic() - t0) * 1000),
            bytes_fetched = len(resp.text or ""),
            extracted_chars = len(resp.text or ""),
        )
        links = domain.parse_index(resp.text or "", base_url = url)
        if not links:
            logger.info(
                f"[tier-2] {url} parsed zero links — likely a long-form "
                f"prose llms.txt; signalling fallback"
            )
            raise domains.dd.ingestion.tiers.errors.EmptyLinksDetected(url)
        filtered = domain.filter_links(links, path_filter)
        if filtered is not None:
            links, listed = filtered
            logger.info(f"[tier-2] {framework_slug}: path_filter kept {len(links)} of {listed} links")
            if not links:
                await progress.finish(status = "downgrade")
                raise domains.dd.ingestion.tiers.errors.TierUnavailable(
                    f"Tier 2: {url} lists {listed} links, none inside the catalog's path_filter"
                )
        if sitemap_url:
            site_pages = await _site_pages(client, sitemap_url, path_filter)
            if site_pages is not None and not domain.covers_site(len(links), site_pages):
                reason = f"the index lists {len(links)} pages, the sitemap {site_pages}"
                logger.info(f"[tier-2] {framework_slug}: {reason} — falling through to the sitemap")
                # An index of markdown files means the site has markdown twins of its pages: the sitemap tier asks for those.
                store.markdown_twins = domain.serves_markdown(links)
                await progress.record_url(
                    url, status = "downgrade", tier = params.TIER_KIND, error_msg = f"{reason}; falling through to the sitemap",
                )
                await progress.finish(status = "downgrade")
                raise domains.dd.ingestion.tiers.errors.TierUnavailable(f"Tier 2: {url}: {reason}")
        # One heading over every page ("Docs") is no section; keep the explorer on the URL folders then.
        links = domain.drop_single_section(links)
        # Author-written index summary (spec v2 blockquote) — stashed on the
        # store for dispatch.finalize; "" when the index carries none.
        try:
            store.index_summary = domain.parse_index_summary(resp.text or "")
        except Exception:
            store.index_summary = ""
        logger.info(f"[tier-2] parsed {len(links)} URLs from {url}")
        await progress.update_total(len(links))
        index_host = domains.dd.ingestion.tiers.fetch.domain.host_of(url)

        async def _announce_wait(seconds: float) -> None:
            logger.info(f"[tier-2] {framework_slug}: {index_host} asked for a pause, waiting {seconds:.0f}s")
            await progress.record_url(
                index_host, status = "rate_limited", tier = params.TIER_KIND,
                error_msg = f"the site asked for a pause: waiting {seconds:.0f}s before going on",
            )

        pacer = domains.dd.ingestion.tiers.fetch.service.Pacer(on_wait = _announce_wait)
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

        async def _bound(title: str, link: str, section: str = "", notes: str = ""):
            nonlocal written
            async with sem:
                await progress.raise_if_cancelled()
                r = await _fetch_one(
                    client,
                    title,
                    link,
                    progress = progress,
                    tier_name = params.TIER_KIND,
                    framework_slug = framework_slug,
                    store = store,
                    index_host = index_host,
                    pacer = pacer,
                    code_language = code_language or "",
                )
            if r is not None:
                slug, src_url, body, t = r
                # Redirect/archived stub (shape guard, Tier-2-only mirror of
                # Tier 1's manifest check) — recorded, never stored.
                if domain.looks_like_redirect_stub(body):
                    await progress.record_url(
                        link,
                        status = "redirect_stub",
                        tier = params.TIER_KIND,
                        bytes_fetched = len((body or "").encode("utf-8")),
                        error_msg = "redirect/archived stub pointer, skipped",
                    )
                # Tier-2-gated stub floor (~1KB): nav fragments / course
                # scaffolding carry no study signal and each burns a full
                # judge+distill cycle downstream — unless the page holds a
                # code example. Tier 1 untouched — its own post.split path
                # keeps the 300B floor.
                elif domain.is_stub(body or ""):
                    await progress.record_url(
                        link,
                        status = "stub_dropped",
                        tier = params.TIER_KIND,
                        bytes_fetched = len((body or "").encode("utf-8")),
                        error_msg = "tier-2 stub (<1KB), skipped",
                    )
                else:
                    slug = await _claim_slug(slug, src_url)
                    await store.add_page(
                        slug = slug,
                        url = src_url,
                        body = body,
                        tier = params.TIER_KIND,
                        title = t,
                        section = section,
                        notes = notes,
                    )
                    written += 1
            await progress.update(current = written, last_url = link)
            return r
        await asyncio.gather(
            *(_bound(t, u, s, n) for t, u, s, n in links),
            return_exceptions = False,
        )
    if written == 0:
        await progress.finish(status = "failed")
        raise RuntimeError(f"Tier 2: {url} all {len(links)} pages failed")
    store.reorder_by_url_list([u for _, u, _, _ in links])
    # No `finish`: post-processing and finalizing follow, and the dispatcher alone marks the run done.
    return written
