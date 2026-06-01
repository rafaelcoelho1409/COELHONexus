"""Tier 4 — httpx-first docs crawler with Crawl4AI Playwright fallback.

Used when the catalog has only a docs landing URL (no llms-full, no
llms-txt, no sitemap). Pipeline:

  Phase 0 — Seed enrichment: probe common docs paths (/docs/, /stable/, …)
            and add 200-responders as extra BFS seeds.
  Phase 1 — Crawl4AI AsyncUrlSeeder: sitemap+CC-based URL discovery.
  Phase 2 — httpx BFS: fills the gap when Phase 1 is sparse.
  Phase 3 — SPA gate: sample-fetch a few URLs; if the majority look like
            unhydrated SPA shells, jump straight to Phase 4b.
  Phase 4a — httpx parallel fetch + markdownify extract (fast static path).
  Phase 4b — Crawl4AI Playwright BFS (remote CDP) fallback. Triggered
             when SPA detected, when Phase 4a fail-rate > 50%, or as the
             last resort if no URLs survived earlier phases.
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

from ..artifacts import extract_and_save_artifacts
from ..extract import extract_title, html_to_markdown
from ..objects_inv import Inventory, fetch_inventory
from ..page_split import maybe_split_page
from ..filters import (
    NON_TARGET_LANGUAGE_PATH_RE,
    build_language_filter,
    is_polyglot,
    same_host,
    should_keep,
)
from ..progress import Progress
from ..seeder import discover_urls as _seeder_discover
from ..sphinx_nav import discover_via_toctree as _toctree_discover
from ..storage import Store


logger = logging.getLogger(__name__)

_USER_AGENT = "COELHONexus-DocsDistiller-Tier4/1.0"
_TIMEOUT_S = 30.0
_CONCURRENCY = 10
_MIN_OK_BYTES = 200
_BFS_MAX_DEPTH = 3
# URL cap removed (2026-05-17) — BFS is already bounded by 4 natural
# guards: same-host filter, subtree path filter, max depth ≤ 3, and the
# `discovered` visited-set (no cycles). The 10000 ceiling was redundant
# and would only truncate genuinely huge docs sites.
_DISCOVERY_MIN_URLS = 5
_PHASE4A_FAIL_RATE_TRIGGER = 0.5     # >50% → escalate to Playwright

_DOCS_PROBES = (
    "/docs/", "/stable/", "/latest/", "/main/",
    "/v1/", "/en/", "/guide/", "/documentation/",
)

_SPA_BODY_MIN = 1500
_SPA_TEXT_MIN = 200
_SPA_ROOT_RE = re.compile(
    r'<div\s+(?:[^>]+\s+)?id\s*=\s*["\']?'
    r'(?:root|app|__next|__nuxt|svelte|main-app|gatsby)'
    r'["\']?\s*[^>]*>\s*</div>',
    re.IGNORECASE,
)
_HYDRATED_SPA_RE = re.compile(
    r'<script[^>]+id\s*=\s*["\']?__NEXT_DATA__'
    r'|window\.__NUXT__\s*='
    r'|window\.___gatsby\s*='
    r'|__remixContext\s*[:=]'
    r'|window\.__INITIAL_STATE__\s*='
    r'|window\.__APOLLO_STATE__\s*=',
    re.IGNORECASE,
)
_SPA_SAMPLE_SIZE = 3


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


# ---------------------------------------------------------------------------
# Phase 0 — seed enrichment
# ---------------------------------------------------------------------------
async def _seed_enrichment(
    docs_url: str, client: httpx.AsyncClient,
) -> list[str]:
    parsed = urlparse(docs_url)
    if parsed.path and parsed.path.rstrip("/") not in ("", "/"):
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [root + p for p in _DOCS_PROBES]

    async def _probe(u: str) -> Optional[str]:
        try:
            r = await client.head(u, timeout=10.0, follow_redirects=True)
            if r.status_code == 405:
                r = await client.get(u, timeout=10.0, follow_redirects=True)
            return str(r.url) if 200 <= r.status_code < 400 else None
        except Exception:
            return None

    results = await asyncio.gather(*(_probe(u) for u in candidates))
    return sorted({r for r in results if r})


# ---------------------------------------------------------------------------
# Phase 2 — bounded BFS
# ---------------------------------------------------------------------------
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
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _fetch_links(url: str) -> list[str]:
        async with sem:
            try:
                r = await client.get(
                    url, timeout=_TIMEOUT_S, follow_redirects=True,
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


# ---------------------------------------------------------------------------
# Phase 3 — SPA detection
# ---------------------------------------------------------------------------
def _looks_like_spa_shell(body: str) -> bool:
    if not body or len(body) < _SPA_BODY_MIN:
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
    if len(visible.strip()) < _SPA_TEXT_MIN:
        return True
    if _SPA_ROOT_RE.search(body):
        return True
    if _HYDRATED_SPA_RE.search(body):
        return True
    return False


async def _is_spa_majority(
    candidates: list[str], client: httpx.AsyncClient,
) -> bool:
    deep = [u for u in candidates if (urlparse(u).path or "").strip("/")]
    sample = (deep or candidates)[:_SPA_SAMPLE_SIZE]
    bodies: list[str] = []
    for u in sample:
        try:
            r = await client.get(u, timeout=_TIMEOUT_S, follow_redirects=True)
            if r.status_code == 200:
                bodies.append(r.text or "")
        except Exception:
            pass
    if not bodies:
        # All sample fetches failed — bias toward safety (treat as SPA so
        # Playwright takes over with browser fingerprint).
        return True
    spa_hits = sum(1 for b in bodies if _looks_like_spa_shell(b))
    return spa_hits >= (len(bodies) // 2 + 1)


# ---------------------------------------------------------------------------
# Phase 4a — httpx parallel fetch + markdownify extract
# ---------------------------------------------------------------------------
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
            url, status="fetch_error", tier="http",
            fetch_ms=int((time.monotonic() - t0) * 1000),
            error_msg=f"{type(e).__name__}: {e}",
        )
        return []
    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            url, status="http_error", tier="http",
            http_code=resp.status_code, fetch_ms=fetch_ms,
            bytes_fetched=len(resp.content or b""),
            error_msg=f"HTTP {resp.status_code}",
        )
        return []
    raw = resp.text or ""
    title = extract_title(raw)
    base_slug = _slugify(title or urlparse(url).path)

    # Artifact extraction — download every <img>/<video>/<audio>/<source>
    # reference (including inline base64 data URLs that bloat notebook
    # pages like UMAP basic_usage.html @ 4.2 MB) to MinIO and rewrite the
    # HTML to use ``/api/v1/.../artifacts/{name}`` paths. Saved markdown
    # then carries our stable references — no upstream-CDN rot, no 4 MB
    # of inline base64 in the digest. Best-effort: a flaky CDN drops the
    # affected asset back to its original URL, page still renders.
    if framework_slug and store is not None:
        try:
            raw, n_artifacts = await extract_and_save_artifacts(
                raw, url, slug=framework_slug, store=store, client=client,
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

    # Anchor-dense / autodoc pages: split into N virtual sub-pages so the
    # digest treats each section as its own source. No-op for ordinary
    # pages. When ``inventory`` is given (Sphinx ``objects.inv`` was
    # available), the splitter uses deterministic per-entity anchors
    # instead of heuristic thresholds.
    try:
        subs = maybe_split_page(raw, url, parent_title=title, inventory=inventory)
    except Exception as e:
        logger.warning(f"[tier-4] page_split failed for {url}: {e}")
        subs = []
    if subs:
        await progress.record_url(
            url, status="success", tier="http",
            http_code=resp.status_code, fetch_ms=fetch_ms,
            bytes_fetched=len(raw),
            extracted_chars=sum(len(s.body_md) for s in subs),
        )
        return [
            (f"{base_slug}--{s.slug_suffix}"[:120], s.sub_url, s.body_md, s.title)
            for s in subs
        ]

    body = html_to_markdown(raw, source_url=url)
    if len(body.encode("utf-8")) < _MIN_OK_BYTES:
        await progress.record_url(
            url, status="extract_empty", tier="http",
            http_code=resp.status_code, fetch_ms=fetch_ms,
            bytes_fetched=len(raw), extracted_chars=len(body),
            error_msg="extracted body too short",
        )
        return []
    await progress.record_url(
        url, status="success", tier="http",
        http_code=resp.status_code, fetch_ms=fetch_ms,
        bytes_fetched=len(raw), extracted_chars=len(body),
    )
    return [(base_slug, url, body, title or base_slug)]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
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
    await progress.start(tier="http", total=0)

    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=httpx.Timeout(_TIMEOUT_S, connect=10.0),
    ) as client:
        # ----------------------------------------------------------------
        # Phases 0 + 1 — collect seeds
        # ----------------------------------------------------------------
        enriched = await _seed_enrichment(url, client)
        seeded = await _seeder_discover(host, raw_path)
        # Phase 1b — Sphinx ``objects.inv`` canonical inventory (L2 of
        # the SOTA cascade). When present, this is the deterministic
        # source-of-truth for every toctree-reachable page and every
        # documented entity anchor — no heuristic DOM parsing required.
        # ``None`` when the site isn't Sphinx-built, in which case Phase
        # 1c (DOM-based ``sphinx_nav``) does the discovery instead.
        docs_root_path = subtree if subtree else "/"
        if not docs_root_path.endswith("/"):
            docs_root_path += "/"
        docs_root = f"{parsed.scheme}://{parsed.netloc}{docs_root_path}"
        inventory = await fetch_inventory(docs_root, client=client)
        # Build an ORDERED list of doc pages from the inventory file —
        # NOT the set returned by `doc_pages()` whose iteration order is
        # non-deterministic. Inventory file order ≈ Sphinx source-tree
        # order ≈ author chapter order, so this is the right tiebreaker
        # for pages NOT in the toctree (orphan std:label refs etc.).
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
        # Phase 1c — DOM-based sidebar+body toctree discovery
        # (fallback when objects.inv absent; complement when present —
        # may catch theme-rendered links the inventory omits).
        toctree = await _toctree_discover(
            url, host=host, subtree=subtree, client=client,
        )
        if toctree:
            logger.info(
                f"[tier-4] toctree sidebar contributed {len(toctree)} URLs"
            )
        # Order-preserving union — `sorted(set(...))` would alphabetize
        # the seeds, which destroys the toctree / inventory chapter
        # order that Bash's GNU multi-page manual depends on. Priority
        # order chosen so the most-author-curated source wins ties:
        # toctree (sphinx_nav DOM, in document order) > objects.inv
        # (Sphinx inventory) > seeder (sitemap+CC) > enrichment > self.
        seeds: list[str] = []
        _seen: set[str] = set()
        for src in (toctree, inv_pages, seeded, enriched, [url]):
            for u in src:
                if u not in _seen:
                    _seen.add(u)
                    seeds.append(u)

        # ----------------------------------------------------------------
        # Phase 2 — httpx BFS to fill the gap if discovery is sparse
        # ----------------------------------------------------------------
        if len(seeds) < _DISCOVERY_MIN_URLS:
            logger.info(
                f"[tier-4] discovery sparse ({len(seeds)} URLs) — "
                f"running httpx BFS from seeds"
            )
            seeds = await _bfs(
                seeds, host=host, subtree=subtree,
                max_depth=_BFS_MAX_DEPTH, client=client,
            )

        # ----------------------------------------------------------------
        # Filters (host / language / blocklist)
        # ----------------------------------------------------------------
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
            await progress.finish(status="failed")
            raise RuntimeError(
                f"Tier 4: no URLs survived filter (host={host}, "
                f"subtree={subtree or '(none)'}, language={language!r})"
            )
        logger.info(
            f"[tier-4] {len(seeds)} discovered → {len(filtered)} after filter"
        )
        # Coverage oracle — when objects.inv was found, compare its
        # canonical page set against what we'll actually crawl. Pages in
        # the inventory but not in ``filtered`` are missed (typically
        # nothing, or release-churn paths the filter intentionally
        # dropped); pages in ``filtered`` but not in the inventory are
        # extras the DOM scrape contributed.
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

        # ----------------------------------------------------------------
        # Phase 3 — SPA gate; if majority look like shells, skip 4a
        # ----------------------------------------------------------------
        spa_majority = await _is_spa_majority(filtered, client)
        if spa_majority:
            logger.info(
                "[tier-4] SPA shells detected (majority of samples) → "
                "falling through to Playwright (Phase 4b)"
            )
            return await _phase4b_playwright(
                filtered, framework_slug=framework_slug,
                progress=progress, store=store,
            )

        # ----------------------------------------------------------------
        # Phase 4a — parallel httpx fetch + extract, streamed to MinIO
        # ----------------------------------------------------------------
        await progress.update_total(len(filtered))
        sem = asyncio.Semaphore(_CONCURRENCY)
        # Stream successful pages to MinIO inside each coroutine; collect
        # failed URLs in a shared list for Phase 4b Playwright escalation.
        # See tier3_sitemap for the broader rationale (bounded memory,
        # partial-persistence on crash, smooth progress bar).
        #
        # TWO counters — they have different units:
        #   urls_done  = number of source URLs whose fetch has FINISHED
        #                (success OR failure). This is the numerator the
        #                progress bar uses against `total = len(filtered)`,
        #                so the percentage stays bounded 0–100%.
        #   written    = number of MinIO writes (= count of saved markdown
        #                pages). One source URL typically writes ONE page,
        #                but inventory-driven page_split inflates autodoc
        #                pages into N virtual sub-pages (Airflow's
        #                example_dags/*.html → 10 sub-pages each, etc.).
        #                Reported as "pages_written" in the final manifest
        #                so the user sees how much corpus was actually
        #                materialized.
        # Before this fix `current = written` and the bar overflowed to
        # `251 / 190 (100%)` on Airflow because the autodoc multiplier
        # outran the URL count.
        written = 0
        urls_done = 0
        failed: list[str] = []

        async def _bound(u: str):
            nonlocal written, urls_done
            try:
                async with sem:
                    await progress.raise_if_cancelled()
                    results = await _fetch_one(
                        client, u, progress=progress, inventory=inventory,
                        framework_slug=framework_slug, store=store,
                    )
                if not results:
                    failed.append(u)
                else:
                    # ``results`` is normally 1-element, but anchor-dense /
                    # autodoc pages explode into N virtual sub-pages (one
                    # source per section) so the digest stage treats them
                    # as distinct documents.
                    for slug, src_url, body, title in results:
                        await store.add_page(
                            slug=slug, url=src_url, body=body,
                            tier="http", title=title,
                        )
                        written += 1
                return results
            finally:
                # Always advance the URL counter (incl. on fetch_error /
                # cancellation / unhandled exception) so the progress bar
                # never gets stuck — `try/finally` is load-bearing here.
                urls_done += 1
                await progress.update(current=urls_done, last_url=u)

        await asyncio.gather(
            *(_bound(u) for u in filtered),
            return_exceptions=False,
        )

    fail_rate = len(failed) / max(1, len(filtered))

    # ----------------------------------------------------------------
    # Phase 4b — Playwright escalation when 4a fails too many URLs
    # ----------------------------------------------------------------
    if fail_rate > _PHASE4A_FAIL_RATE_TRIGGER and failed:
        logger.warning(
            f"[tier-4] phase 4a fail-rate {fail_rate*100:.0f}% "
            f"({len(failed)}/{len(filtered)}) — escalating failed URLs to "
            f"Playwright (Phase 4b)"
        )
        # Mid-run progress reset for the new tier (different total).
        try:
            extra = await _phase4b_playwright(
                failed, framework_slug=framework_slug,
                progress=progress, store=store,
            )
            written += extra
        except Exception as e:
            logger.warning(f"[tier-4] Phase 4b also failed: {e}")

    if written == 0:
        await progress.finish(status="failed")
        raise RuntimeError(
            f"Tier 4: all {len(filtered)} URL fetches failed in both 4a + 4b"
        )

    # Re-sort the manifest into discovery order. ``asyncio.gather`` /
    # ``_bound`` raced the per-URL fetches; the first one to complete
    # got idx=0, etc. — so without this step the saved manifest lists
    # pages in network-completion order, not the chapter order encoded
    # in ``filtered`` (which inherited toctree / inventory ordering).
    # ``filtered`` is the authoritative chapter sequence here.
    store.reorder_by_url_list(filtered)
    await progress.finish(status="done")
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
    from .tier4_playwright import crawl_urls
    written, failed = await crawl_urls(
        urls,
        framework_slug=framework_slug,
        progress=progress, store=store,
        min_ok_bytes=_MIN_OK_BYTES,
    )
    logger.info(
        f"[tier-4] Playwright phase 4b: {written} written, {len(failed)} failed"
    )
    return written
