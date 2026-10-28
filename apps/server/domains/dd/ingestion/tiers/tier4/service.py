"""Tier 4 — I/O shell: httpx-first crawler with Playwright fallback.
Phases: 0 docs-path probe, 1 seeder+inventory+toctree, 2 BFS fill, 3 SPA
gate, 4a parallel httpx, 4b Playwright on SPA/high-fail. Pure helpers
(slugify, link extraction, SPA heuristic, run-config assembly) live in
domain.py."""
from __future__ import annotations
import domains, infra
from . import domain, params
from .. import fetch

import asyncio
import logging
import time
import uuid
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)


logger = logging.getLogger(__name__)


async def _install_resource_blocker(crawler) -> None:
    """Block Next.js prefetch + images/fonts/CSS via Playwright route()."""
    async def _hook(page, context, **_):
        try:
            for pattern in params.BLOCKED_ROUTES:
                await context.route(pattern, lambda r: r.abort())
        except Exception as e:
            logger.warning(f"[playwright] route-hook failed (non-fatal): {e}")
    try:
        crawler.crawler_strategy.set_hook("on_page_context_created", _hook)
    except Exception as e:
        logger.warning(f"[playwright] could not install route hook: {e}")


def _build_md_generator():
    """PruningContentFilter + DefaultMarkdownGenerator."""
    return infra.playwright.crawl4ai.service.DefaultMarkdownGenerator(
        content_filter = infra.playwright.crawl4ai.service.PruningContentFilter(
            threshold = params.MD_PRUNING_THRESHOLD,
            threshold_type = params.MD_PRUNING_THRESHOLD_TYPE,
            min_word_threshold = params.MD_PRUNING_MIN_WORDS,
        ),
    )


def _build_dispatcher(max_session_permit: int, rate_delay: tuple[float, float], rate_max_delay: float):
    """crawl4ai's memory-adaptive dispatcher: `max_session_permit` pages at once, a random delay in `rate_delay` between requests."""
    crawl4ai = infra.playwright.crawl4ai.service
    return crawl4ai.MemoryAdaptiveDispatcher(
        max_session_permit = max_session_permit,
        memory_threshold_percent = params.MEMORY_THRESHOLD_PERCENT,
        recovery_threshold_percent = params.RECOVERY_THRESHOLD_PERCENT,
        check_interval = params.DISPATCH_CHECK_INTERVAL_S,
        rate_limiter = crawl4ai.RateLimiter(
            base_delay = rate_delay,
            max_delay = rate_max_delay,
            max_retries = params.RATE_MAX_RETRIES,
        ),
    )


async def _arun_many(crawler, urls: list[str], cfg, dispatcher, session_prefix: str):
    """Start the browser pass over `urls`: each URL gets a session of its own when the config can be cloned per URL."""
    if hasattr(cfg, "clone"):
        config = [cfg.clone(session_id = f"{session_prefix}-{uuid.uuid4().hex[: params.SESSION_ID_CHARS]}") for _ in urls]
    else:
        config = cfg
    return await crawler.arun_many(urls, config = config, dispatcher = dispatcher)


async def crawl_urls(
    urls: list[str],
    *,
    framework_slug: str,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    store: domains.dd.ingestion.storage.service.Store,
    min_ok_bytes: int = params.DEFAULT_MIN_OK_BYTES,
    code_language: str = "",
) -> tuple[int, list[str]]:
    """Crawl URLs via Playwright; stream successes to store immediately. Transient navigation failures get one retry with longer timeouts. Returns (pages_written, failed_urls).
    A page is converted from the HTML the browser rendered with the same extractor every other tier uses (chrome removed, the
    page's own H1 kept, code blocks labelled) — crawl4ai's markdown kept TypeScript's cookie banner (the title of 13 pages) and its footer."""
    if not urls:
        return 0, []

    browser_cfg = infra.playwright.crawl4ai.service.build_browser_config()
    md_generator = _build_md_generator()
    primary_cfg, retry_cfg = domain.build_run_configs(
        infra.playwright.crawl4ai.service.CrawlerRunConfig,
        infra.playwright.crawl4ai.service.CacheMode,
        infra.playwright.crawl4ai.service.LXMLWebScrapingStrategy(),
        md_generator,
    )

    dispatcher_primary = _build_dispatcher(params.MAX_SESSION_PERMIT, params.PRIMARY_RATE_DELAY_S, params.PRIMARY_RATE_MAX_DELAY_S)
    dispatcher_retry = _build_dispatcher(params.RETRY_SESSION_PERMIT, params.RETRY_RATE_DELAY_S, params.RETRY_RATE_MAX_DELAY_S)
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
                if domain.is_transient_nav_error(err) and label == "primary":
                    transient_nav_failures.append(url)
                else:
                    failed.append(url)
                await progress.record_url(
                    url,
                    status = "fetch_error",
                    tier = params.BROWSER_TIER,
                    error_msg = err[: params.ERROR_MAX_CHARS],
                )
                continue

            extract = domains.dd.ingestion.tiers.extract.domain
            rendered = getattr(r, "html", "") or ""
            title = ""
            body = extract.html_to_markdown(rendered, source_url = url, default_code_language = code_language) if rendered else ""
            if rendered:
                title = extract.extract_title(rendered)
            if len(body.encode("utf-8")) < min_ok_bytes:
                # The rendered HTML gave nothing: what crawl4ai made of the page is better than nothing.
                body = domain.fallback_markdown(r)
            if len(body.encode("utf-8")) < min_ok_bytes:
                failed.append(url)
                await progress.record_url(
                    url,
                    status = "extract_empty",
                    tier = params.BROWSER_TIER,
                    extracted_chars = len(body),
                    error_msg = f"body too short ({len(body)}B)",
                )
                continue

            slug = domain.slugify(urlparse(url).path or framework_slug)
            await store.add_page(
                slug = slug,
                url = url,
                body = body,
                tier = params.BROWSER_TIER,
                title = title or slug,
            )
            written += 1
            await progress.record_url(
                url,
                status = "success",
                tier = params.BROWSER_TIER,
                extracted_chars = len(body),
            )
            await progress.update(current = written, last_url = url)
    await progress.start(tier = params.BROWSER_TIER, total = len(urls))
    async with infra.playwright.crawl4ai.service.AsyncWebCrawler(config = browser_cfg) as crawler:
        await _install_resource_blocker(crawler)
        stream = await _arun_many(crawler, urls, primary_cfg, dispatcher_primary, "crawl")
        await _consume(stream, "primary")
        if transient_nav_failures:
            logger.info(
                f"[playwright] retry pass: {len(transient_nav_failures)} URLs "
                f"with longer timeouts"
            )
            await asyncio.sleep(params.RETRY_DELAY_S)
            stream = await _arun_many(crawler, transient_nav_failures, retry_cfg, dispatcher_retry, "retry")
            await _consume(stream, "retry")
    if written == 0:
        await progress.finish(status = "failed")
    # Otherwise no `finish`: the run goes on (post-processing, finalizing) and the dispatcher alone marks it done.
    return written, failed


@retry(
    reraise = True,
    retry = retry_if_exception_type(fetch.params.RETRYABLE_ERRORS),
    stop = stop_after_attempt(params.GET_ATTEMPTS),
    wait = wait_exponential_jitter(initial = params.GET_WAIT_INITIAL_S, max = params.GET_WAIT_MAX_S),
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": params.USER_AGENT})


async def _seeder_discover(
    host: str,
    docs_path: str,
    *,
    max_urls: int = params.SEEDER_MAX_URLS,
) -> list[str]:
    """Crawl4AI seeder URL list. [] on any failure — caller falls back to BFS.
    Import is deferred so tiers 1/2/3 don't pay the crawl4ai load cost."""
    cfg = infra.playwright.crawl4ai.service.SeedingConfig(
        source = params.SEEDER_SOURCE,
        pattern = domain.seed_pattern_for(docs_path),
        max_urls = max_urls,
        extract_head = False,
    )
    try:
        async with infra.playwright.crawl4ai.service.AsyncUrlSeeder() as seeder:
            results = await seeder.urls(host, cfg)
    except Exception as e:
        logger.warning(f"[seeder] {host} discovery failed: {e}")
        return []
    out = [
        d.get("url") for d in (results or [])
        if d.get("url") and d.get("status") in params.SEEDER_OK_STATUSES
    ]
    logger.info(f"[seeder] {host} found {len(out)} URLs (pattern={cfg.pattern!r})")
    return out


async def _seed_enrichment(
    docs_url: str, client: httpx.AsyncClient,
) -> list[str]:
    candidates = domain.probe_candidates(docs_url)

    async def _probe(u: str) -> str | None:
        try:
            r = await client.head(u, timeout = params.PROBE_TIMEOUT_S, follow_redirects = True)
            if r.status_code == 405:
                r = await client.get(u, timeout = params.PROBE_TIMEOUT_S, follow_redirects = True)
            return str(r.url) if 200 <= r.status_code < 400 else None
        except Exception:
            return None

    results = await asyncio.gather(*(_probe(u) for u in candidates))
    return sorted({r for r in results if r})


async def _bfs(
    seeds: list[str],
    *,
    host: str,
    subtree: str,
    max_depth: int,
    client: httpx.AsyncClient,
) -> list[str]:
    discovered: dict[str, int] = {u: 0 for u in seeds}
    queue: list[tuple[str, int]] = [(u, 0) for u in seeds]
    sem = asyncio.Semaphore(params.CONCURRENCY)

    async def _fetch_links(url: str) -> list[str]:
        async with sem:
            try:
                r = await client.get(
                    url, timeout = params.TIMEOUT_S, follow_redirects = True,
                )
            except Exception:
                return []
            if r.status_code != 200:
                return []
            ctype = (r.headers.get("content-type") or "").lower()
            if "html" not in ctype:
                return []
            return domain.extract_links(r.text or "", url)

    while queue:
        batch = queue
        queue = []
        results = await asyncio.gather(*(_fetch_links(u) for u, _ in batch))
        for (url, depth), links in zip(batch, results):
            if depth >= max_depth:
                continue
            for link in links:
                if not domain.crawlable_link(link, host, subtree):
                    continue
                if link not in discovered:
                    discovered[link] = depth + 1
                    queue.append((link, depth + 1))
    return sorted(discovered.keys())


async def _is_spa_majority(
    candidates: list[str], client: httpx.AsyncClient,
) -> bool:
    bodies: list[str] = []
    for u in domain.spa_sample(candidates):
        try:
            r = await client.get(u, timeout = params.TIMEOUT_S, follow_redirects = True)
            if r.status_code == 200:
                bodies.append(r.text or "")
        except Exception:
            pass
    return domain.is_spa_majority(bodies)


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    *,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    inventory: domains.dd.ingestion.tiers.tier4.sphinx.entities.Inventory | None = None,
    framework_slug: str | None = None,
    store: domains.dd.ingestion.storage.service.Store | None = None,
    code_language: str = "",
) -> list[tuple[str, str, str, str]]:
    """Fetch + extract a URL. Returns [(slug, url, body_md, title), ...]; N entries when page_split fires (autodoc/anchored H2), empty list on failure."""
    t0 = time.monotonic()
    try:
        resp = await _get(client, url)
    except Exception as e:
        await progress.record_url(
            url,
            status = "fetch_error",
            tier = params.TIER_KIND,
            fetch_ms = int((time.monotonic() - t0) * 1000),
            error_msg = f"{type(e).__name__}: {e}",
        )
        return []
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
        return []
    raw = resp.text or ""
    title = domains.dd.ingestion.tiers.extract.domain.extract_title(raw)
    base_slug = domain.page_slug(title, url)
    if framework_slug and store is not None:
        try:
            raw, n_artifacts = await domains.dd.ingestion.artifacts.service.extract_and_save_artifacts(
                raw,
                url,
                slug = framework_slug,
                store = store,
                client = client,
            )
            if n_artifacts:
                logger.info(
                    f"[tier-4] {url}: saved {n_artifacts} artifact(s) "
                    f"to ingestion/{framework_slug}/artifacts/"
                )
        except Exception as e:
            logger.warning(
                f"[tier-4] artifact extraction failed for {url}: "
                f"{type(e).__name__}: {e}"
            )
    # Anchor-dense / autodoc pages → N virtual sub-pages (no-op otherwise).
    try:
        subs = domains.dd.ingestion.tiers.tier4.sphinx.domain.maybe_split_page(
            raw, url, parent_title = title, inventory = inventory, default_code_language = code_language,
        )
    except Exception as e:
        logger.warning(f"[tier-4] page_split failed for {url}: {e}")
        subs = []
    if subs:
        await progress.record_url(
            url,
            status = "success",
            tier = params.TIER_KIND,
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(raw),
            extracted_chars = sum(len(s.body_md) for s in subs),
        )
        return [(domain.sub_slug(base_slug, s.slug_suffix), s.sub_url, s.body_md, s.title) for s in subs]
    body = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(raw, source_url = url, default_code_language = code_language)
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
        return []
    await progress.record_url(
        url,
        status = "success",
        tier = params.TIER_KIND,
        http_code = resp.status_code,
        fetch_ms = fetch_ms,
        bytes_fetched = len(raw),
        extracted_chars = len(body),
    )
    return [(base_slug, url, body, title or base_slug)]


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
    code_language = code_language or ""
    parsed = urlparse(url)
    host = domains.dd.ingestion.tiers.fetch.domain.host_of(url)
    if not host:
        raise RuntimeError(f"Tier 4: cannot parse host from url={url!r}")
    raw_path = parsed.path or "/"
    subtree = domain.docs_subtree(parsed.path)
    logger.info(
        f"[tier-4] framework={framework_slug} host={host} "
        f"subtree={subtree or '(none)'} url={url}"
    )
    await progress.start(tier = params.TIER_KIND, total = 0)
    async with httpx.AsyncClient(
        headers = {"User-Agent": params.USER_AGENT, "Accept": params.ACCEPT},
        timeout = httpx.Timeout(params.TIMEOUT_S, connect = params.CONNECT_TIMEOUT_S),
        # Go's 36 `http_error` pages were all 301s (`/doc/articles/go_command.html` → its post in the blog): a redirect is a page.
        follow_redirects = True,
    ) as client:
        enriched = await _seed_enrichment(url, client)
        seeded = await _seeder_discover(host, raw_path)
        # Sphinx objects.inv — deterministic page + anchor catalog when present.
        docs_root = domain.docs_root(parsed.scheme, parsed.netloc, subtree)
        sphinx = domains.dd.ingestion.tiers.tier4.sphinx
        inventory = await sphinx.service.fetch_inventory(docs_root, client = client)
        # Inventory iteration order ≈ Sphinx source-tree order; doc_pages() returns a non-deterministic set.
        inv_pages: list[str] = sphinx.domain.ordered_doc_pages(inventory) if inventory else []
        if inventory:
            logger.info(
                f"[tier-4] objects.inv: {inventory.project} "
                f"v{inventory.version} — {len(inv_pages)} doc pages, "
                f"{len(inventory.entities)} entities"
            )
        # DOM toctree — fallback when no inventory; complement when present.
        toctree = await sphinx.service.discover_via_toctree(
            url,
            host = host,
            subtree = subtree,
            client = client,
        )
        if toctree:
            logger.info(
                f"[tier-4] toctree sidebar contributed {len(toctree)} URLs"
            )
        # Priority: most-author-curated wins (see `domain.merge_seeds` for the order and fragment rules).
        seeds, anchors_by_page = domain.merge_seeds([toctree, inv_pages, seeded, enriched, [url]])
        if len(seeds) < params.DISCOVERY_MIN_URLS:
            logger.info(
                f"[tier-4] discovery sparse ({len(seeds)} URLs) — "
                f"running httpx BFS from seeds"
            )
            seeds = await _bfs(
                seeds,
                host = host,
                subtree = subtree,
                max_depth = params.BFS_MAX_DEPTH,
                client = client,
            )
        allow, deny = domains.dd.ingestion.filters.domain.build_language_filter(language)
        polyglot = domains.dd.ingestion.filters.domain.is_polyglot(framework_name or "")

        filtered = [
            u for u in seeds
            if domain.wanted(u, host, path_filter, polyglot = polyglot, language = language, allow = allow, deny = deny)
        ]
        if not filtered:
            await progress.finish(status = "failed")
            raise RuntimeError(
                f"Tier 4: no URLs survived filter (host={host}, "
                f"subtree={subtree or '(none)'}, language={language!r})"
            )
        logger.info(
            f"[tier-4] {len(seeds)} discovered → {len(filtered)} after filter"
        )
        if inventory:
            inv_set = inventory.doc_pages()
            n_crawled, gap, extras = domain.oracle_gap(inv_set, filtered)
            logger.info(
                f"[tier-4 oracle] inventory={len(inv_set)} pages, "
                f"crawling={n_crawled}, missing={len(gap)}, "
                f"extras-from-dom={len(extras)}"
            )
            for u in sorted(gap)[:10]:
                logger.info(f"[tier-4 oracle]   MISSING: {u}")
        spa_majority = await _is_spa_majority(filtered, client)
        if spa_majority:
            logger.info(
                "[tier-4] SPA shells detected (majority of samples) → "
                "falling through to Playwright (Phase 4b)"
            )
            browser_written = await _phase4b_playwright(
                filtered,
                framework_slug = framework_slug,
                progress = progress,
                store = store,
                code_language = code_language,
            )
            if browser_written == 0:
                # Bash's run ended `done` with 0 pages: this early return skipped the check every other path makes.
                await progress.finish(status = "failed")
                raise RuntimeError(f"Tier 4: the browser fetched none of the {len(filtered)} pages")
            return browser_written
        await progress.update_total(len(filtered))
        sem = asyncio.Semaphore(params.CONCURRENCY)
        # urls_done = progress-bar denom; written = MinIO pages (page_split makes written > urls_done — bar overflowed 251/190 on Airflow autodoc).
        written = 0
        urls_done = 0
        failed: list[str] = []

        async def _bound(u: str):
            nonlocal written, urls_done
            try:
                async with sem:
                    await progress.raise_if_cancelled()
                    results = await _fetch_one(
                        client,
                        u,
                        progress = progress,
                        inventory = inventory,
                        framework_slug = framework_slug,
                        store = store,
                        code_language = code_language,
                    )
                if not results:
                    failed.append(u)
                else:
                    for slug, src_url, body, title in results:
                        base, anchors = domain.page_identity(src_url, anchors_by_page)
                        await store.add_page(
                            slug = slug,
                            url = base,
                            body = body,
                            tier = params.TIER_KIND,
                            title = title,
                            anchors = anchors,
                        )
                        written += 1
                return results
            finally:
                # try/finally is load-bearing — the bar must advance on errors too.
                urls_done += 1
                await progress.update(current = urls_done, last_url = u)

        await asyncio.gather(
            *(_bound(u) for u in filtered),
            return_exceptions = False,
        )
    if domain.should_escalate(len(failed), len(filtered)):
        fail_rate = len(failed) / max(1, len(filtered))
        logger.warning(
            f"[tier-4] phase 4a fail-rate {fail_rate*100:.0f}% "
            f"({len(failed)}/{len(filtered)}) — escalating failed URLs to "
            f"Playwright (Phase 4b)"
        )
        try:
            extra = await _phase4b_playwright(
                failed,
                framework_slug = framework_slug,
                progress = progress,
                store = store,
                code_language = code_language,
            )
            written += extra
        except Exception as e:
            logger.warning(f"[tier-4] Phase 4b also failed: {e}")
    if written == 0:
        await progress.finish(status = "failed")
        raise RuntimeError(
            f"Tier 4: all {len(filtered)} URL fetches failed in both 4a + 4b"
        )
    # Restore discovery (chapter) order — gather raced the fetches.
    store.reorder_by_url_list(filtered)
    # No `finish`: post-processing and finalizing follow, and the dispatcher alone marks the run done.
    return written


async def _phase4b_playwright(
    urls: list[str],
    *,
    framework_slug: str,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    store: domains.dd.ingestion.storage.service.Store,
    code_language: str = "",
) -> int:
    written, failed = await crawl_urls(
        urls,
        framework_slug = framework_slug,
        progress = progress,
        store = store,
        min_ok_bytes = params.MIN_OK_BYTES,
        code_language = code_language,
    )
    logger.info(
        f"[tier-4] Playwright phase 4b: {written} written, {len(failed)} failed"
    )
    return written
