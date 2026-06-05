"""Tier 4 — httpx-first docs crawler with Crawl4AI Playwright fallback.

Used when only a docs landing URL is available. Phases: 0 docs-path probe,
1 Crawl4AI seeder + Sphinx objects.inv + DOM toctree, 2 BFS fill,
3 SPA gate, 4a parallel httpx fetch, 4b Playwright on SPA / high failure.
"""
import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ...artifacts import extract_and_save_artifacts
from ..extract import extract_title, html_to_markdown
from ...filters import (
    NON_TARGET_LANGUAGE_PATH_RE,
    build_language_filter,
    is_polyglot,
    same_host,
    should_keep,
)
from ...progress import Progress
from ...storage import Store
from .params import (
    BFS_MAX_DEPTH,
    CONCURRENCY,
    DISCOVERY_MIN_URLS,
    DOCS_PROBES,
    MIN_OK_BYTES,
    PHASE4A_FAIL_RATE_TRIGGER,
    SPA_BODY_MIN,
    SPA_SAMPLE_SIZE,
    SPA_TEXT_MIN,
    TIMEOUT_S,
    USER_AGENT,
)
from .patterns import HYDRATED_SPA_RE, SPA_ROOT_RE
from .sphinx.inventory import Inventory, fetch_inventory
from .sphinx.nav import discover_via_toctree as _toctree_discover
from .sphinx.page_split import maybe_split_page


logger = logging.getLogger(__name__)


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial=1, max=8),
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": USER_AGENT})


def _seed_pattern_for(path: str) -> Optional[str]:
    """Subtree path → seeder glob. Bare root → None (full-domain search)."""
    cleaned = (path or "").rstrip("/")
    if not cleaned or cleaned in ("/", ""):
        return None
    return f"*{cleaned}*"


async def _seeder_discover(
    host: str,
    docs_path: str,
    *,
    max_urls: int = 10_000_000,
) -> list[str]:
    """Crawl4AI seeder URL list. [] on any failure — caller falls back to BFS.
    Import is deferred so tiers 1/2/3 don't pay the crawl4ai load cost."""
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
    logger.info(f"[seeder] {host} found {len(out)} URLs (pattern={cfg.pattern!r})")
    return out


async def _seed_enrichment(
    docs_url: str, client: httpx.AsyncClient,
) -> list[str]:
    parsed = urlparse(docs_url)
    if parsed.path and parsed.path.rstrip("/") not in ("", "/"):
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [root + p for p in DOCS_PROBES]

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


def _extract_links(html: str, base_url: str) -> list[str]:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        full = urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            continue
        out.append(full.split("#", 1)[0])
    return out


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
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _fetch_links(url: str) -> list[str]:
        async with sem:
            try:
                r = await client.get(
                    url, timeout = TIMEOUT_S, follow_redirects = True,
                )
            except Exception:
                return []
            if r.status_code != 200:
                return []
            ctype = (r.headers.get("content-type") or "").lower()
            if "html" not in ctype:
                return []
            return _extract_links(r.text or "", url)

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


def _looks_like_spa_shell(body: str) -> bool:
    if not body or len(body) < SPA_BODY_MIN:
        return True
    no_script = re.sub(
        r"<script[^>]*>.*?</script>", "", body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    no_style = re.sub(
        r"<style[^>]*>.*?</style>", "", no_script,
        flags=re.DOTALL | re.IGNORECASE,
    )
    visible = re.sub(r"<[^>]+>", " ", no_style)
    if len(visible.strip()) < SPA_TEXT_MIN:
        return True
    if SPA_ROOT_RE.search(body):
        return True
    if HYDRATED_SPA_RE.search(body):
        return True
    return False


async def _is_spa_majority(
    candidates: list[str], client: httpx.AsyncClient,
) -> bool:
    deep = [u for u in candidates if (urlparse(u).path or "").strip("/")]
    sample = (deep or candidates)[:SPA_SAMPLE_SIZE]
    bodies: list[str] = []
    for u in sample:
        try:
            r = await client.get(u, timeout = TIMEOUT_S, follow_redirects = True)
            if r.status_code == 200:
                bodies.append(r.text or "")
        except Exception:
            pass
    if not bodies:
        # All fetches failed — bias to safety; Playwright will retry with browser fingerprint.
        return True
    spa_hits = sum(1 for b in bodies if _looks_like_spa_shell(b))
    return spa_hits >= (len(bodies) // 2 + 1)


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    *,
    progress: Progress,
    inventory: Inventory | None = None,
    framework_slug: str | None = None,
    store: Store | None = None,
) -> list[tuple[str, str, str, str]]:
    """Fetch + extract. Returns a list of ``(slug, src_url, body_md, title)``:
    one entry per page in the common case, or N entries when the page
    matches an anchor-dense / autodoc split pattern (see
    ``page_split.maybe_split_page``). Empty list on fetch / extract failure.
    """
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
    title = extract_title(raw)
    base_slug = _slugify(title or urlparse(url).path)
    if framework_slug and store is not None:
        try:
            raw, n_artifacts = await extract_and_save_artifacts(
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
        subs = maybe_split_page(raw, url, parent_title = title, inventory = inventory)
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
    body = html_to_markdown(raw, source_url = url)
    if len(body.encode("utf-8")) < MIN_OK_BYTES:
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
    progress: Progress,
    store: Store,
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
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout = httpx.Timeout(TIMEOUT_S, connect = 10.0),
    ) as client:
        enriched = await _seed_enrichment(url, client)
        seeded = await _seeder_discover(host, raw_path)
        # Sphinx objects.inv — deterministic page + anchor catalog when present.
        docs_root_path = subtree if subtree else "/"
        if not docs_root_path.endswith("/"):
            docs_root_path += "/"
        docs_root = f"{parsed.scheme}://{parsed.netloc}{docs_root_path}"
        inventory = await fetch_inventory(docs_root, client = client)
        # Inventory iteration order ≈ Sphinx source-tree order ≈ author chapter
        # order; doc_pages() returns a set whose order is non-deterministic.
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
        toctree = await _toctree_discover(
            url, 
            host = host, 
            subtree = subtree, 
            client = client,
        )
        if toctree:
            logger.info(
                f"[tier-4] toctree sidebar contributed {len(toctree)} URLs"
            )
        # Order-preserving union — sorting alphabetizes & breaks chapter order
        # (e.g. Bash GNU multi-page manual). Priority: most-author-curated wins.
        seeds: list[str] = []
        _seen: set[str] = set()
        for src in (toctree, inv_pages, seeded, enriched, [url]):
            for u in src:
                if u not in _seen:
                    _seen.add(u)
                    seeds.append(u)
        if len(seeds) < DISCOVERY_MIN_URLS:
            logger.info(
                f"[tier-4] discovery sparse ({len(seeds)} URLs) — "
                f"running httpx BFS from seeds"
            )
            seeds = await _bfs(
                seeds, 
                host = host, 
                subtree = subtree,
                max_depth = BFS_MAX_DEPTH, 
                client = client,
            )
        allow, deny = build_language_filter(language)
        polyglot = is_polyglot(framework_name or "")

        def _keep(u: str) -> bool:
            p = urlparse(u)
            if not same_host(u, host):
                return False
            if NON_TARGET_LANGUAGE_PATH_RE.search(p.path or ""):
                return False
            # Stage 1 noise filter — defaults + per-framework path_filter.
            from ..filters import passes_path_filter
            if not passes_path_filter(u, path_filter):
                return False
            if polyglot and language:
                return should_keep(u, allow, deny)
            if allow or deny:
                return should_keep(u, allow, deny)
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
        # Coverage oracle: log inventory pages we dropped + DOM extras we added.
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
        sem = asyncio.Semaphore(CONCURRENCY)
        # urls_done = source URLs finished (progress-bar denominator);
        # written  = MinIO pages written (≥ urls_done when page_split fires).
        # Without two counters the bar overflowed 251/190 on Airflow autodoc.
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
    if fail_rate > PHASE4A_FAIL_RATE_TRIGGER and failed:
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
    progress: Progress,
    store: Store,
) -> int:
    """Deferred-import wrapper around playwright_crawl.crawl_urls so the
    heavy crawl4ai dependency only loads on the SPA / high-fail-rate
    code paths."""
    from .playwright import crawl_urls
    written, failed = await crawl_urls(
        urls,
        framework_slug = framework_slug,
        progress = progress, 
        store = store,
        min_ok_bytes = MIN_OK_BYTES,
    )
    logger.info(
        f"[tier-4] Playwright phase 4b: {written} written, {len(failed)} failed"
    )
    return written
