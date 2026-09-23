"""Tier 4 — I/O shell: httpx-first crawler with Playwright fallback.
Phases: 0 docs-path probe, 1 seeder+inventory+toctree, 2 BFS fill, 3 SPA
gate, 4a parallel httpx, 4b Playwright on SPA/high-fail. Pure helpers
(slugify, link extraction, SPA heuristic, run-config assembly) live in
domain.py."""
from __future__ import annotations
import domains
from . import domain, params

import asyncio
import json
import logging
import os
import ssl
import time
import uuid
from typing import Optional
from urllib.parse import urlparse
from urllib.request import urlopen

import httpx
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
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)


logger = logging.getLogger(__name__)


# BrowserConfig needs wss://…/devtools/browser/<id> from CDP /json/version.
# Cached per worker.
_cdp_cached: dict[str, str] = {}


def resolve_cdp_ws_url(cdp_http_url: str) -> Optional[str]:
    """HTTP CDP URL → wss://…/devtools/browser/<id>. None on any failure so
    the caller falls back to local Chromium (or skips Playwright)."""
    if not cdp_http_url:
        return None
    if cdp_http_url in _cdp_cached:
        return _cdp_cached[cdp_http_url]
    parsed = urlparse(cdp_http_url)
    json_url = f"{cdp_http_url.rstrip('/')}/json/version"
    try:
        # Tailscale ingress sometimes serves internal certs.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urlopen(json_url, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        ws_url = data.get("webSocketDebuggerUrl", "")
        if not ws_url:
            logger.warning(f"[cdp] no webSocketDebuggerUrl at {json_url}")
            return None
        ws_path = urlparse(ws_url).path
        scheme = "wss" if parsed.scheme == "https" else "ws"
        resolved = f"{scheme}://{parsed.netloc}{ws_path}"
        _cdp_cached[cdp_http_url] = resolved
        return resolved
    except Exception as e:
        logger.warning(f"[cdp] resolve failed for {cdp_http_url}: {e}")
        return None


def _build_browser_config():
    """Build BrowserConfig using remote CDP if available, else local."""
    cdp_http = (os.environ.get("PLAYWRIGHT_CDP_HEADLESS") or "").strip() or None
    cdp_ws = resolve_cdp_ws_url(cdp_http) if cdp_http else None
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
    proxy = (os.environ.get("BROWSER_PROXY_URL") or "").strip() or None
    return BrowserConfig(
        browser_type = "chromium",
        headless = True,
        verbose = False,
        proxy = proxy,
    )


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
    """PruningContentFilter + DefaultMarkdownGenerator."""
    return DefaultMarkdownGenerator(
        content_filter = PruningContentFilter(
            threshold = 0.45,
            threshold_type = "dynamic",
            min_word_threshold = 5,
        ),
    )


async def crawl_urls(
    urls: list[str],
    *,
    framework_slug: str,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    store: domains.dd.ingestion.storage.service.Store,
    min_ok_bytes: int = params.DEFAULT_MIN_OK_BYTES,
) -> tuple[int, list[str]]:
    """Crawl URLs via Playwright; stream successes to store immediately. Transient navigation failures get one retry with longer timeouts. Returns (pages_written, failed_urls)."""
    if not urls:
        return 0, []

    browser_cfg = _build_browser_config()
    md_generator = _build_md_generator()
    primary_cfg, retry_cfg = domain.build_run_configs(
        CrawlerRunConfig,
        CacheMode,
        LXMLWebScrapingStrategy(),
        md_generator,
    )

    dispatcher_primary = MemoryAdaptiveDispatcher(
        max_session_permit = params.MAX_SESSION_PERMIT,
        memory_threshold_percent = 85.0,
        recovery_threshold_percent = 75.0,
        check_interval = 1.0,
        rate_limiter = RateLimiter(
            base_delay = (0.5, 1.5),
            max_delay = 20.0,
            max_retries = 3,
        ),
    )
    dispatcher_retry = MemoryAdaptiveDispatcher(
        max_session_permit = 1,
        memory_threshold_percent = 85.0,
        recovery_threshold_percent = 75.0,
        check_interval = 1.0,
        rate_limiter = RateLimiter(
            base_delay = (1.0, 3.0),
            max_delay = 30.0,
            max_retries = 3,
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
                    url,
                    status = "fetch_error",
                    tier = "playwright",
                    error_msg = err[:300],
                )
                continue

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
                    url,
                    status = "extract_empty",
                    tier = "playwright",
                    extracted_chars = len(body),
                    error_msg = f"body too short ({len(body)}B)",
                )
                continue

            slug = domain.slugify(urlparse(url).path or framework_slug)
            await store.add_page(
                slug = slug,
                url = url,
                body = body,
                tier = "playwright",
                title = slug,
            )
            written += 1
            await progress.record_url(
                url,
                status = "success",
                tier = "playwright",
                extracted_chars = len(body),
            )
            await progress.update(current = written, last_url = url)
    await progress.start(tier = "playwright", total = len(urls))
    async with AsyncWebCrawler(config = browser_cfg) as crawler:
        await _install_resource_blocker(crawler)
        if hasattr(primary_cfg, "clone"):
            per_url_configs = [
                primary_cfg.clone(session_id = f"crawl-{uuid.uuid4().hex[:12]}")
                for _ in urls
            ]
            stream = await crawler.arun_many(
                urls,
                config = per_url_configs,
                dispatcher = dispatcher_primary,
            )
        else:
            stream = await crawler.arun_many(
                urls,
                config = primary_cfg,
                dispatcher = dispatcher_primary,
            )
        await _consume(stream, "primary")
        if transient_nav_failures:
            logger.info(
                f"[playwright] retry pass: {len(transient_nav_failures)} URLs "
                f"with longer timeouts"
            )
            await asyncio.sleep(params.RETRY_DELAY_S)
            if hasattr(retry_cfg, "clone"):
                per_url_retry = [
                    retry_cfg.clone(session_id = f"retry-{uuid.uuid4().hex[:12]}")
                    for _ in transient_nav_failures
                ]
                stream = await crawler.arun_many(
                    transient_nav_failures,
                    config = per_url_retry,
                    dispatcher = dispatcher_retry,
                )
            else:
                stream = await crawler.arun_many(
                    transient_nav_failures,
                    config = retry_cfg,
                    dispatcher = dispatcher_retry,
                )
            await _consume(stream, "retry")
    if written == 0:
        await progress.finish(status = "failed")
    else:
        await progress.finish(status = "done")
    return written, failed


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial=1, max=8),
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": params.USER_AGENT})


async def _seeder_discover(
    host: str,
    docs_path: str,
    *,
    max_urls: int = 10_000_000,
) -> list[str]:
    """Crawl4AI seeder URL list. [] on any failure — caller falls back to BFS.
    Import is deferred so tiers 1/2/3 don't pay the crawl4ai load cost."""
    cfg = SeedingConfig(
        source="sitemap+cc",
        pattern=domain.seed_pattern_for(docs_path),
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
    logger.info(f"[seeder] {host} found {len(out)} URLs (pattern={cfg.pattern!r})")
    return out


async def _seed_enrichment(
    docs_url: str, client: httpx.AsyncClient,
) -> list[str]:
    parsed = urlparse(docs_url)
    if parsed.path and parsed.path.rstrip("/") not in ("", "/"):
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [root + p for p in params.DOCS_PROBES]

    async def _probe(u: str) -> Optional[str]:
        try:
            r = await client.head(u, timeout = 10.0, follow_redirects = True)
            if r.status_code == 405:
                r = await client.get(u, timeout = 10.0, follow_redirects = True)
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
                p = urlparse(link)
                if (p.netloc or "").lower() != host:
                    continue
                if subtree and not (p.path or "").startswith(subtree):
                    continue
                if link not in discovered:
                    discovered[link] = depth + 1
                    queue.append((link, depth + 1))
    return sorted(discovered.keys())


async def _is_spa_majority(
    candidates: list[str], client: httpx.AsyncClient,
) -> bool:
    deep = [u for u in candidates if (urlparse(u).path or "").strip("/")]
    sample = (deep or candidates)[:params.SPA_SAMPLE_SIZE]
    bodies: list[str] = []
    for u in sample:
        try:
            r = await client.get(u, timeout = params.TIMEOUT_S, follow_redirects = True)
            if r.status_code == 200:
                bodies.append(r.text or "")
        except Exception:
            pass
    if not bodies:
        # All fetches failed — bias to safety; Playwright will retry with browser fingerprint.
        return True
    spa_hits = sum(1 for b in bodies if domain.looks_like_spa_shell(b))
    return spa_hits >= (len(bodies) // 2 + 1)


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    *,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    inventory: domains.dd.ingestion.tiers.tier4.sphinx.entities.Inventory | None = None,
    framework_slug: str | None = None,
    store: domains.dd.ingestion.storage.service.Store | None = None,
) -> list[tuple[str, str, str, str]]:
    """Fetch + extract a URL. Returns [(slug, url, body_md, title), ...]; N entries when page_split fires (autodoc/anchored H2), empty list on failure."""
    t0 = time.monotonic()
    try:
        resp = await _get(client, url)
    except Exception as e:
        await progress.record_url(
            url,
            status = "fetch_error",
            tier = "http",
            fetch_ms = int((time.monotonic() - t0) * 1000),
            error_msg = f"{type(e).__name__}: {e}",
        )
        return []
    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            url,
            status = "http_error",
            tier = "http",
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(resp.content or b""),
            error_msg = f"HTTP {resp.status_code}",
        )
        return []
    raw = resp.text or ""
    title = domains.dd.ingestion.tiers.extract.domain.extract_title(raw)
    base_slug = domain.slugify(title or urlparse(url).path)
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
        subs = domains.dd.ingestion.tiers.tier4.sphinx.domain.maybe_split_page(raw, url, parent_title = title, inventory = inventory)
    except Exception as e:
        logger.warning(f"[tier-4] page_split failed for {url}: {e}")
        subs = []
    if subs:
        await progress.record_url(
            url,
            status = "success",
            tier = "http",
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(raw),
            extracted_chars = sum(len(s.body_md) for s in subs),
        )
        return [
            (f"{base_slug}--{s.slug_suffix}"[:120], s.sub_url, s.body_md, s.title)
            for s in subs
        ]
    body = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(raw, source_url = url)
    if len(body.encode("utf-8")) < params.MIN_OK_BYTES:
        await progress.record_url(
            url,
            status = "extract_empty",
            tier = "http",
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
        tier = "http",
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
) -> int:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 4: cannot parse host from url={url!r}")
    raw_path = parsed.path or "/"
    subtree = raw_path.rsplit("/", 1)[0] if "/" in raw_path else ""
    if subtree in ("/", ""):
        subtree = ""
    logger.info(
        f"[tier-4] framework={framework_slug} host={host} "
        f"subtree={subtree or '(none)'} url={url}"
    )
    await progress.start(tier = "http", total = 0)
    async with httpx.AsyncClient(
        headers = {"User-Agent": params.USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout = httpx.Timeout(params.TIMEOUT_S, connect = 10.0),
    ) as client:
        enriched = await _seed_enrichment(url, client)
        seeded = await _seeder_discover(host, raw_path)
        # Sphinx objects.inv — deterministic page + anchor catalog when present.
        docs_root_path = subtree if subtree else "/"
        if not docs_root_path.endswith("/"):
            docs_root_path += "/"
        docs_root = f"{parsed.scheme}://{parsed.netloc}{docs_root_path}"
        inventory = await domains.dd.ingestion.tiers.tier4.sphinx.service.fetch_inventory(docs_root, client = client)
        # Inventory iteration order ≈ Sphinx source-tree order; doc_pages() returns a non-deterministic set.
        inv_pages: list[str] = []
        if inventory:
            _seen_inv: set[str] = set()
            for ent in inventory.entities:
                if ent.role in ("std:doc", "std:label") and ent.page_url \
                        and ent.page_url not in _seen_inv:
                    _seen_inv.add(ent.page_url)
                    inv_pages.append(ent.page_url)
        if inventory:
            logger.info(
                f"[tier-4] objects.inv: {inventory.project} "
                f"v{inventory.version} — {len(inv_pages)} doc pages, "
                f"{len(inventory.entities)} entities"
            )
        # DOM toctree — fallback when no inventory; complement when present.
        toctree = await domains.dd.ingestion.tiers.tier4.sphinx.service.discover_via_toctree(
            url,
            host = host,
            subtree = subtree,
            client = client,
        )
        if toctree:
            logger.info(
                f"[tier-4] toctree sidebar contributed {len(toctree)} URLs"
            )
        # Order-preserving union; sorting alphabetizes and breaks chapter order (Bash GNU regression). Priority: most-author-curated wins.
        seeds: list[str] = []
        _seen: set[str] = set()
        for src in (toctree, inv_pages, seeded, enriched, [url]):
            for u in src:
                if u not in _seen:
                    _seen.add(u)
                    seeds.append(u)
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

        def _keep(u: str) -> bool:
            p = urlparse(u)
            if not domains.dd.ingestion.filters.domain.same_host(u, host):
                return False
            if domains.dd.ingestion.filters.patterns.NON_TARGET_LANGUAGE_PATH_RE.search(p.path or ""):
                return False
            # Stage 1 noise filter — defaults + per-framework path_filter.
            if not domains.dd.ingestion.filters.domain.passes_path_filter(u, path_filter):
                return False
            if polyglot and language:
                return domains.dd.ingestion.filters.domain.should_keep(u, allow, deny)
            if allow or deny:
                return domains.dd.ingestion.filters.domain.should_keep(u, allow, deny)
            return True

        filtered = [u for u in seeds if _keep(u)]
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
            kept_set = {u.split("#", 1)[0] for u in filtered}
            gap = inv_set - kept_set
            extras = kept_set - inv_set
            logger.info(
                f"[tier-4 oracle] inventory={len(inv_set)} pages, "
                f"crawling={len(kept_set)}, missing={len(gap)}, "
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
            return await _phase4b_playwright(
                filtered,
                framework_slug = framework_slug,
                progress = progress,
                store = store,
            )
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
                    )
                if not results:
                    failed.append(u)
                else:
                    for slug, src_url, body, title in results:
                        await store.add_page(
                            slug = slug,
                            url = src_url,
                            body = body,
                            tier = "http",
                            title = title,
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
    fail_rate = len(failed) / max(1, len(filtered))
    if fail_rate > params.PHASE4A_FAIL_RATE_TRIGGER and failed:
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
    await progress.finish(status = "done")
    return written


async def _phase4b_playwright(
    urls: list[str],
    *,
    framework_slug: str,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    store: domains.dd.ingestion.storage.service.Store,
) -> int:
    written, failed = await crawl_urls(
        urls,
        framework_slug = framework_slug,
        progress = progress,
        store = store,
        min_ok_bytes = params.MIN_OK_BYTES,
    )
    logger.info(
        f"[tier-4] Playwright phase 4b: {written} written, {len(failed)} failed"
    )
    return written
