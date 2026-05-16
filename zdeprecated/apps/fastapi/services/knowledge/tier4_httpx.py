"""
Tier 4 ingestion — httpx-first with Crawl4AI Playwright fallback for SPAs.

Most pure-T4 sites in the catalog are static-rendered Sphinx/Hugo/MkDocs
sites that simply don't ship a sitemap.xml. Playwright-everywhere is
overkill for them. This module:

  Phase 0 — seed enrichment: when docs_url is bare-host or "/" path,
            probe common docs paths (/docs/, /stable/, /latest/, ...)
            and add 200-responders as additional seeds.
  Phase 1 — AsyncUrlSeeder discovery (sitemap + Common Crawl) ∪ enriched
            seeds ∪ user-supplied docs_url.
  Phase 2 — httpx-based BFS as fallback when discovery yields too few
            URLs. Depth-bounded, host-constrained, BeautifulSoup link
            extraction.
  Phase 3 — SPA gate: sample-fetch first N URLs; if ANY look like SPA
            shells, fall back to existing Crawl4AI Playwright pipeline
            (`_ingest_crawl4ai`).
  Phase 4a — httpx parallel fetch + Crawl4AI DefaultMarkdownGenerator
             extraction (static path).
  Phase 4b — `_ingest_crawl4ai` (existing Playwright path; SPA / unsafe).

Quality is identical between 4a and 4b — same Crawl4AI extractor singleton
in `services/knowledge/markdown_extractor.py`. Only fetch infrastructure
differs.

Decision context: docs/KNOWLEDGE-DISTILLER-MARKDOWN-EXTRACTOR-MIGRATION.md
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from schemas.knowledge.ingestion import (
    DocsIngestionConfig,
    IngestResult,
    ManifestEntry,
)
from services.knowledge.ingest_progress import IngestProgress
from services.knowledge.ingestion import (
    NON_TARGET_LANGUAGE_PATH_RE,
    _build_language_filter,
    _is_polyglot_framework,
    _should_keep,
    _slugify,
    _write_raw,
)
from services.knowledge.markdown_extractor import html_to_markdown
from services.knowledge.storage import MinIOStudyStorage

logger = logging.getLogger(__name__)


_USER_AGENT = "COELHONexus-KD-Tier4/1.0"
_HTTP_TIMEOUT = 30.0
_MAX_CONCURRENT = 10

# Phase 0 — common docs path probes for seed enrichment.
# When docs_url is bare-host or "/" path, we test these subpaths and add
# any 200-responders as additional BFS seeds.
_DOCS_PROBES = [
    "/docs/",
    "/stable/",
    "/latest/",
    "/main/",
    "/v1/",
    "/en/",
    "/guide/",
    "/documentation/",
]

# Phase 2 — discovery threshold; below this we fall back to httpx BFS.
_DISCOVERY_MIN_URLS = 5

# Phase 3 — SPA detection thresholds. Conservative bias: false-positive
# (mistakenly call a static site SPA) just wastes Playwright time
# (no harm); false-negative (mistakenly call SPA static) yields empty
# corpus (silent failure). When in doubt, treat as SPA.
_SPA_BODY_MIN = 1500          # bodies smaller than this are suspicious
_SPA_TEXT_MIN = 200           # visible text after stripping tags+scripts
_SPA_ROOT_RE = re.compile(
    r'<div\s+(?:[^>]+\s+)?id\s*=\s*["\']?'
    r'(?:root|app|__next|__nuxt|svelte|main-app|gatsby)'
    r'["\']?\s*[^>]*>\s*</div>',
    re.IGNORECASE,
)
# Hydrated-SPA markers — page is SSR'd (content visible) BUT navigation/links
# are injected via client-side hydration. httpx BFS finds only top-of-page
# links; Playwright rendering needed for full nav discovery. Real cases:
#   - Next.js docs sites (HashiCorp, Vercel, OpenAI, many Mintlify-on-Next)
#     → <script id="__NEXT_DATA__" type="application/json">
#   - Nuxt.js (Vue ecosystem) → window.__NUXT__ = {...}
#   - Gatsby → window.___gatsby = {...}
#   - Remix → __remixContext
#   - Generic SSR with SPA hydration → window.__INITIAL_STATE__ / __APOLLO_STATE__
_HYDRATED_SPA_RE = re.compile(
    r'<script[^>]+id\s*=\s*["\']?__NEXT_DATA__'   # Next.js
    r'|window\.__NUXT__\s*='                       # Nuxt
    r'|window\.___gatsby\s*='                      # Gatsby
    r'|__remixContext\s*[:=]'                      # Remix
    r'|window\.__INITIAL_STATE__\s*='              # generic SSR/SPA
    r'|window\.__APOLLO_STATE__\s*=',              # Apollo SSR
    re.IGNORECASE,
)
_SPA_SAMPLE_SIZE = 3


# =============================================================================
# Phase 0 — seed enrichment
# =============================================================================
async def _seed_enrichment(
    docs_url: str,
    client: httpx.AsyncClient) -> list[str]:
    """
    When `docs_url` is at the host root or shallow, probe common docs paths
    and return 200-responders. Skipped when docs_url already has a deep path.
    """
    parsed = urlparse(docs_url)
    if parsed.path and parsed.path.rstrip("/") not in ("", "/"):
        return []  # already a deep path; no enrichment needed
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [host_root + p for p in _DOCS_PROBES]

    async def _probe(url: str) -> Optional[str]:
        try:
            r = await client.head(url, timeout = 10.0, follow_redirects = True)
            if r.status_code == 405:
                r = await client.get(url, timeout = 10.0, follow_redirects = True)
            return str(r.url) if 200 <= r.status_code < 400 else None
        except Exception:
            return None

    results = await asyncio.gather(*(_probe(u) for u in candidates))
    enriched = sorted({r for r in results if r})
    if enriched:
        logger.info(
            f"[tier-4] seed enrichment: {len(enriched)} additional paths discovered"
        )
        for u in enriched:
            logger.info(f"[tier-4]   → {u}")
    return enriched


# =============================================================================
# Phase 2 — httpx BFS (fallback discovery)
# =============================================================================
def _extract_links(html: str, base_url: str) -> list[str]:
    """
    Extract `<a href>` links from raw HTML. Tries BeautifulSoup first
    (more robust on real-world docs HTML); falls back to regex if bs4
    is unavailable for some reason.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _regex_extract_links(html, base_url)

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return _regex_extract_links(html, base_url)

    out: list[str] = []
    for a in soup.find_all("a", href = True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        full = urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            continue
        full = full.split("#", 1)[0]   # strip fragment
        out.append(full)
    return out


def _regex_extract_links(html: str, base_url: str) -> list[str]:
    """Regex fallback when bs4 is unavailable."""
    pattern = re.compile(r'<a\s+[^>]*?href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
    out: list[str] = []
    for href in pattern.findall(html):
        href = href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        full = urljoin(base_url, href)
        if not full.startswith(("http://", "https://")):
            continue
        out.append(full.split("#", 1)[0])
    return out


async def _httpx_bfs(
    seed_urls: list[str],
    *,
    host: str,
    subtree_path: str,
    max_depth: int,
    client: httpx.AsyncClient) -> list[str]:
    """
    BFS via httpx + bs4 link extraction. Same-host, subtree-bounded,
    depth-bounded. Returns discovered URLs (including seeds).
    """
    discovered: dict[str, int] = {u: 0 for u in seed_urls}
    queue: list[tuple[str, int]] = [(u, 0) for u in seed_urls]
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _fetch_links(url: str) -> list[str]:
        async with sem:
            try:
                r = await client.get(url, timeout = _HTTP_TIMEOUT, follow_redirects = True)
            except Exception:
                return []
            if r.status_code != 200:
                return []
            ctype = r.headers.get("content-type", "").lower()
            if "html" not in ctype:
                return []
            return _extract_links(r.text, url)

    while queue:
        # Pop one BFS layer at a time so depth bookkeeping is clean.
        batch = queue
        queue = []
        results = await asyncio.gather(*(_fetch_links(u) for u, _ in batch))
        for (url, depth), links in zip(batch, results):
            if depth >= max_depth:
                continue
            for link in links:
                p = urlparse(link)
                if p.netloc.lower() != host:
                    continue
                if subtree_path and not p.path.startswith(subtree_path):
                    continue
                if link not in discovered:
                    discovered[link] = depth + 1
                    queue.append((link, depth + 1))
    logger.info(
        f"[tier-4] httpx BFS expanded {len(seed_urls)} seeds → "
        f"{len(discovered)} URLs (max_depth={max_depth})"
    )
    return sorted(discovered.keys())


# =============================================================================
# Phase 3 — SPA detection
# =============================================================================
def _looks_like_spa_shell(body: str) -> bool:
    """
    Conservative SPA-shell heuristic. Returns True when body looks like a
    JS-driven page that needs Playwright rendering for full link discovery.

    Three signals (any one fires):
      1. body too small / visible text too sparse → empty SPA shell
      2. empty <div id="root|app|__next|__nuxt|...">  </div> → unhydrated shell
      3. hydrated SPA bundle markers (__NEXT_DATA__, __NUXT__, ___gatsby,
         __remixContext, __INITIAL_STATE__, __APOLLO_STATE__) → page IS
         SSR'd but nav/links inject via client-side JS — httpx BFS will
         miss most of the actual navigation

    Bias toward Playwright fallback: false-positive (Playwright on a real
    static site) just costs wall time; false-negative (httpx on real SPA)
    produces empty/incomplete corpus.
    """
    if not body or len(body) < _SPA_BODY_MIN:
        return True
    no_script = re.sub(
        r"<script[^>]*>.*?</script>", "", body, flags = re.DOTALL | re.IGNORECASE,
    )
    no_style = re.sub(
        r"<style[^>]*>.*?</style>", "", no_script, flags = re.DOTALL | re.IGNORECASE,
    )
    visible = re.sub(r"<[^>]+>", " ", no_style)
    if len(visible.strip()) < _SPA_TEXT_MIN:
        return True
    if _SPA_ROOT_RE.search(body):
        return True
    # Hydrated-SPA detection: page renders SSR'd content but nav/links
    # injected via client-side hydration. httpx BFS would find only top-of-
    # page links; Playwright needed for full discovery.
    if _HYDRATED_SPA_RE.search(body):
        return True
    return False


# =============================================================================
# Phase 4a — httpx parallel fetch + Crawl4AI extract
# =============================================================================
async def _httpx_fetch_and_extract_all(
    urls: list[str],
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage,
    client: httpx.AsyncClient) -> IngestResult:
    """
    Parallel httpx fetch + Crawl4AI markdown extraction. Returns
    IngestResult with manifest entries. Raises RuntimeError if the
    failure rate exceeds 50% so the caller can fall back to Playwright.
    """
    progress = IngestProgress(cfg.study_id)
    await progress.start(tier = "crawl4ai", total = len(urls))

    sem = asyncio.Semaphore(_MAX_CONCURRENT)
    failures: list[tuple[str, str]] = []
    # Pre-allocated by URL position so manifest order tracks the input
    # discovery order (sitemap / Common Crawl / BFS sequence) instead of
    # fetch-completion order under the parallel Semaphore. The
    # zero-padded ordinal in each slug then makes alphabetical
    # filesystem listing equal that same order — same rule applied in
    # post_ingest (Tier 1), ingest_llms_txt (Tier 2), ingest_sitemap_httpx
    # (Tier 3).
    width = max(4, len(str(max(0, len(urls) - 1))))
    entries: list[Optional[ManifestEntry]] = [None] * len(urls)
    total_bytes = 0
    completed = 0

    async def _one(i: int, url: str) -> None:
        nonlocal total_bytes, completed
        async with sem:
            t0 = time.monotonic()
            try:
                r = await client.get(url, timeout = _HTTP_TIMEOUT, follow_redirects = True)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                failures.append((url, err))
                completed += 1
                await progress.update(completed, f"(failed) {url}")
                await progress.record_url(
                    url, status="fetch_error", tier="crawl4ai",
                    fetch_ms=int((time.monotonic() - t0) * 1000),
                    error_msg=err,
                )
                return
            fetch_ms = int((time.monotonic() - t0) * 1000)
            if r.status_code != 200:
                failures.append((url, f"HTTP {r.status_code}"))
                completed += 1
                await progress.update(completed, f"(failed) {url}")
                await progress.record_url(
                    url, status="http_error", tier="crawl4ai",
                    http_code=r.status_code, fetch_ms=fetch_ms,
                    bytes_fetched=len(r.text or ""),
                    error_msg=f"HTTP {r.status_code}",
                )
                return
            content = html_to_markdown(r.text, url)
            if not content:
                failures.append((url, "empty extraction"))
                completed += 1
                await progress.update(completed, f"(empty) {url}")
                await progress.record_url(
                    url, status="extract_empty", tier="crawl4ai",
                    http_code=r.status_code, fetch_ms=fetch_ms,
                    bytes_fetched=len(r.text or ""), extracted_chars=0,
                    error_msg="empty extraction",
                )
                return
            slug = f"{i:0{width}d}-{_slugify(url)}"
            entry = await _write_raw(
                storage = storage,
                study_root = cfg.study_root,
                slug = slug,
                content = content,
                url = url,
                tier = "crawl4ai",
                cfg = cfg,
            )
            if entry:
                entries[i] = entry
                total_bytes += entry.bytes
            completed += 1
            await progress.update(completed, url)
            await progress.record_url(
                url, status="success", tier="crawl4ai",
                http_code=r.status_code, fetch_ms=fetch_ms,
                bytes_fetched=len(r.text or ""), extracted_chars=len(content),
            )

    await asyncio.gather(*(_one(i, u) for i, u in enumerate(urls)))

    # Preserve submission order; drop slots that failed (None).
    manifest: list[ManifestEntry] = [e for e in entries if e is not None]

    fail_rate = len(failures) / max(1, len(urls))
    if fail_rate > 0.5:
        await progress.finish(status = "failed")
        raise RuntimeError(
            f"Tier 4 httpx: {fail_rate*100:.0f}% failure rate "
            f"({len(failures)}/{len(urls)}) — falling back to Playwright"
        )
    logger.info(
        f"[tier-4] httpx OK — {len(manifest)} files, {total_bytes} bytes "
        f"({len(failures)} failures, {fail_rate*100:.0f}%)"
    )
    await progress.finish(status = "done")
    return IngestResult(
        tier_used = "crawl4ai",
        total_files = len(manifest),
        total_bytes = total_bytes,
        manifest = manifest,
        skipped_urls = [u for u, _ in failures],
    )


# =============================================================================
# Public entry point
# =============================================================================
async def ingest_tier4(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage,
    cache = None) -> IngestResult:
    """
    Tier 4 entry point — httpx-first orchestration with Playwright fallback.

    Phase 0: seed enrichment (probe common docs paths)
    Phase 1: AsyncUrlSeeder (Crawl4AI: sitemap + Common Crawl)
    Phase 2: httpx BFS if discovery is sparse (< _DISCOVERY_MIN_URLS)
    Phase 3: SPA gate (sample-fetch first N URLs)
    Phase 4a: httpx parallel fetch + Crawl4AI extract (static path)
    Phase 4b: Crawl4AI Playwright BFS (existing path; SPA / unsafe)
    """
    parsed = urlparse(cfg.docs_url)
    host = (parsed.netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 4: cannot parse host from docs_url={cfg.docs_url!r}")

    raw_path = parsed.path or "/"
    subtree_path = raw_path.rsplit("/", 1)[0] if "/" in raw_path else ""
    if subtree_path in ("/", ""):
        subtree_path = ""

    logger.info(
        f"[tier-4] start framework={cfg.framework!r} url={cfg.docs_url} "
        f"host={host} subtree={subtree_path or '(none)'}"
    )

    # Single client for all httpx work in this tier (Phase 0/2/3/4a).
    async with httpx.AsyncClient(
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout = httpx.Timeout(_HTTP_TIMEOUT, connect = 10.0),
    ) as client:
        # ---------------------------------------------------------------
        # Phase 0 — seed enrichment
        # ---------------------------------------------------------------
        enriched = await _seed_enrichment(cfg.docs_url, client)
        seeds = sorted({cfg.docs_url, *enriched})

        # ---------------------------------------------------------------
        # Phase 1 — AsyncUrlSeeder discovery (sitemap + Common Crawl)
        # ---------------------------------------------------------------
        seeded_urls: list[str] = []
        try:
            from crawl4ai import AsyncUrlSeeder, SeedingConfig
            seed_pattern = f"*{subtree_path}*" if subtree_path else None
            seeder_cfg = SeedingConfig(
                source = "sitemap+cc",
                pattern = seed_pattern,
                max_urls = 10_000_000,
                extract_head = False,
            )
            async with AsyncUrlSeeder() as seeder:
                results = await seeder.urls(host, seeder_cfg)
            seeded_urls = [
                d.get("url") for d in results
                if d.get("url") and d.get("status") in ("valid", "found")
            ]
            logger.info(
                f"[tier-4] AsyncUrlSeeder found {len(seeded_urls)} URLs "
                f"(pattern={seed_pattern!r})"
            )
        except Exception as e:
            logger.warning(f"[tier-4] AsyncUrlSeeder failed: {e}")

        # ---------------------------------------------------------------
        # Phase 2 — httpx live BFS if discovery is sparse
        # ---------------------------------------------------------------
        candidates = sorted(set(seeds + seeded_urls))
        if len(candidates) < _DISCOVERY_MIN_URLS:
            logger.info(
                f"[tier-4] discovery sparse ({len(candidates)} URLs) — "
                f"running httpx BFS from seeds"
            )
            bfs_urls = await _httpx_bfs(
                candidates,
                host = host,
                subtree_path = subtree_path,
                max_depth = cfg.max_depth,
                client = client,
            )
            candidates = sorted(set(candidates + bfs_urls))

        # ---------------------------------------------------------------
        # Apply existing T4 filters (host, language, blocklist)
        # ---------------------------------------------------------------
        allow, deny = _build_language_filter(cfg.language)
        allow.extend(cfg.extra_allow_patterns)
        deny.extend(cfg.extra_deny_patterns)
        polyglot = _is_polyglot_framework(cfg.framework)

        def _keep(u: str) -> bool:
            p = urlparse(u)
            if (p.netloc or "").lower() != host:
                return False
            if NON_TARGET_LANGUAGE_PATH_RE.search(p.path or ""):
                return False
            if polyglot and cfg.language:
                if not _should_keep(u, allow, deny):
                    return False
            elif allow or deny:
                if not _should_keep(u, allow, deny):
                    return False
            return True

        filtered = [u for u in candidates if _keep(u)]
        logger.info(
            f"[tier-4] post-filter: {len(filtered)}/{len(candidates)} URLs "
            f"(host + language + blocklist)"
        )

        # If everything got filtered out, fall back to the existing
        # Playwright BFS — same behavior as before this module existed.
        if not filtered:
            logger.warning(
                f"[tier-4] no URLs survived filter; falling back to "
                f"Crawl4AI Playwright BFS"
            )
            from services.knowledge.ingestion import _ingest_crawl4ai
            return await _ingest_crawl4ai(cfg, storage, cache)

        # ---------------------------------------------------------------
        # Phase 3 — SPA gate
        #
        # Sample SPA-detection on URLs DEEPER than the bare host. The bare
        # host root is often a redirect/landing page that's tiny (Hugo,
        # MkDocs, GitBook frequently 302 the root → /stable/ or /v1/).
        # A tiny landing page would falsely trip the SPA detector even
        # though the rest of the site is static. Sample real content URLs.
        #
        # Also require MAJORITY of samples to look like SPA shells before
        # falling back, instead of ANY single positive — a single anomalous
        # page (e.g., empty 404, redirect intermediary) shouldn't downgrade
        # the whole run to slow Playwright.
        # ---------------------------------------------------------------
        deep_pool = [
            u for u in filtered
            if (urlparse(u).path or "").strip("/") not in ("", "")
        ]
        sample_pool = deep_pool if deep_pool else filtered
        sample_urls = sample_pool[:_SPA_SAMPLE_SIZE]
        sample_bodies: list[str] = []
        for u in sample_urls:
            try:
                r = await client.get(u, timeout = _HTTP_TIMEOUT, follow_redirects = True)
                if r.status_code == 200:
                    sample_bodies.append(r.text)
            except Exception:
                pass

        if not sample_bodies:
            # All sample fetches failed — could be an SPA, could be transient
            # network issues. Bias toward safety: use Playwright.
            logger.info(
                f"[tier-4] SPA gate: 0/{len(sample_urls)} sample fetches "
                f"succeeded → Crawl4AI Playwright fallback"
            )
            from services.knowledge.ingestion import _ingest_crawl4ai
            return await _ingest_crawl4ai(cfg, storage, cache)

        spa_hits = sum(1 for b in sample_bodies if _looks_like_spa_shell(b))
        majority_threshold = len(sample_bodies) // 2 + 1   # strict majority (>50%)
        if spa_hits >= majority_threshold:
            logger.info(
                f"[tier-4] SPA gate: {spa_hits}/{len(sample_bodies)} samples "
                f"are SPA shells (>= majority {majority_threshold}) → "
                f"Crawl4AI Playwright fallback"
            )
            from services.knowledge.ingestion import _ingest_crawl4ai
            return await _ingest_crawl4ai(cfg, storage, cache)

        logger.info(
            f"[tier-4] SPA gate: {len(sample_bodies) - spa_hits}/{len(sample_bodies)} "
            f"samples are static (spa_hits={spa_hits} < majority {majority_threshold}) "
            f"→ httpx parallel fetch path"
        )

        # ---------------------------------------------------------------
        # Phase 4a — httpx parallel fetch + Crawl4AI extract
        # ---------------------------------------------------------------
        try:
            return await _httpx_fetch_and_extract_all(filtered, cfg, storage, client)
        except RuntimeError as e:
            logger.warning(
                f"[tier-4] httpx fetch path failed ({e}); "
                f"falling back to Crawl4AI Playwright"
            )
            from services.knowledge.ingestion import _ingest_crawl4ai
            return await _ingest_crawl4ai(cfg, storage, cache)
