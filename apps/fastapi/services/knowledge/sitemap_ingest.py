"""
Knowledge Distiller — Tier 3 Ingestion (sitemap.xml httpx fast path)

Dispatched by `services/knowledge/ingestion.py` when the resolver assigned
`tier == 3` — meaning Stage D's content-validated probe confirmed the site
publishes a real `/sitemap.xml` but no `/llms-full.txt` or `/llms.txt`.

PIPELINE (~2-5 min for typical ~100-500 page docs, vs ~20 min Tier 4 Playwright):
  1. GET host-root `/sitemap.xml` (resolver confirmed VALID)
  2. Parse <loc> entries; recursively unwrap <sitemapindex> → sub-sitemaps
  3. Filter URLs to the docs subtree + language + extra allow/deny patterns
  4. Parallel httpx GET each URL (Semaphore cap) with tenacity retries
  5. trafilatura extracts markdown from each HTML response (pure-Python,
     F1 ≈ 0.791 on docs). rs-trafilatura would lift that to 0.859-0.966
     but only ships cp312 wheels as of 0.1.1 — we're on cp313. Swap is
     one line in `_extract_markdown` below once rs-trafilatura publishes
     cp313 wheels.
  6. Empty-content guard + `_write_raw()` to MinIO
  7. Partial-failure policy: continue; abort only if <50% succeed

WHY NO PLAYWRIGHT: the resolver's D-probe ALREADY verified this host serves
a real sitemap + root-liveness is LIVE (≥2 docs signals, ≥400 chars of text,
not parked/SPA-shell). Sites that land on Tier 3 are by construction static
HTML — no JS rendering needed. httpx beats Playwright by ~10x wall time
with identical content quality.

LANGUAGE SCOPING: reuses the same polyglot detection and path filters as
Tier 4 (`_build_language_filter`, `_is_polyglot_framework`, `_should_keep`
from `ingestion.py`). A user asking about "OpenTelemetry Python" gets
/python/ URLs only, not /java/ /go/ /ruby/.

RATE LIMITING: Semaphore(_MAX_CONCURRENT=10) caps in-flight fetches per
study. No per-host token bucket needed — a single study targets one host.
tenacity retries on timeouts / network errors only (not on 4xx, which are
permanent).

Reference: docs/KNOWLEDGE-DISTILLER-INGESTION-PIPELINE-PLAN.md §Step 4
"""
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from schemas.knowledge.ingestion import (
    DocsIngestionConfig,
    IngestResult,
    ManifestEntry,
)
from services.knowledge.ingest_progress import IngestProgress
from services.knowledge.ingestion import (
    _build_language_filter,
    _is_polyglot_framework,
    _matches_any,
    _should_keep,
    _slugify,
    _write_raw,
)
from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================
_USER_AGENT = "COELHONexus-KD-Tier3/1.0"
_HTTP_TIMEOUT = 30.0
# Rate-limiting strategy — intentional deviation from the plan's aiolimiter
# spec. The plan prescribes aiolimiter token buckets for per-host fairness,
# but a single study only hits one host, so Semaphore(N) is functionally
# identical and simpler. Migrate to aiolimiter if/when multi-study parallel
# ingestion lands (would need per-host fairness across concurrent studies).
_MAX_CONCURRENT = 10             # parallel page fetches — polite single-host load
_MAX_SITEMAP_DEPTH = 3           # bound sitemap-index recursion (defensive)
_MAX_URLS_PER_SITEMAP = 50_000   # hard cap per sitemap expansion
_MIN_OK_RATIO = 0.5              # abort tier only if <50% of pages succeeded


# =============================================================================
# Sitemap parsing (with recursive sitemapindex unwrap)
# =============================================================================
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


def _extract_loc_urls(body: str) -> list[str]:
    """
    Parse <loc> entries from a sitemap XML body. Falls back to regex when
    the document is malformed (happens more than you'd think in the wild).
    Returns deduped http(s) URLs in document order.
    """
    urls: list[str] = []
    try:
        root = ET.fromstring(body)
        for elem in root.iter():
            tag = elem.tag.lower().rsplit("}", 1)[-1]  # strip xmlns
            if tag == "loc" and elem.text and elem.text.strip():
                urls.append(elem.text.strip())
    except ET.ParseError:
        urls = _LOC_RE.findall(body)

    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _body_is_sitemap_index(body: str) -> bool:
    """Cheap check — <sitemapindex> anywhere in the first 2KB of body."""
    return "<sitemapindex" in body[:2000].lower()


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 15),
)
async def _fetch_text(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": _USER_AGENT})


async def _expand_sitemap(
    client: httpx.AsyncClient,
    sitemap_url: str,
    depth: int = 0) -> list[str]:
    """
    Fetch one sitemap. If it's a sitemapindex, recurse into each child
    sitemap up to _MAX_SITEMAP_DEPTH. Returns all <loc> URLs found across
    the tree.
    """
    if depth >= _MAX_SITEMAP_DEPTH:
        logger.warning(f"[tier-3] sitemap recursion depth cap hit at {sitemap_url}")
        return []
    try:
        resp = await _fetch_text(client, sitemap_url)
    except Exception as e:
        logger.warning(f"[tier-3] sitemap fetch failed {sitemap_url}: {e}")
        return []
    if resp.status_code != 200:
        logger.info(f"[tier-3] sitemap {sitemap_url} → HTTP {resp.status_code}")
        return []

    body = resp.text
    urls = _extract_loc_urls(body)
    if not urls:
        return []

    if _body_is_sitemap_index(body):
        # Each <loc> here is another sitemap — recurse in parallel
        logger.info(
            f"[tier-3] sitemap-index {sitemap_url} → {len(urls)} sub-sitemaps "
            f"(depth={depth})"
        )
        children = await asyncio.gather(
            *(_expand_sitemap(client, u, depth + 1) for u in urls[:500]),
            return_exceptions = True,
        )
        out: list[str] = []
        for c in children:
            if isinstance(c, list):
                out.extend(c)
        # Dedupe + cap
        seen: set[str] = set()
        deduped: list[str] = []
        for u in out:
            if u in seen:
                continue
            seen.add(u)
            deduped.append(u)
            if len(deduped) >= _MAX_URLS_PER_SITEMAP:
                break
        return deduped

    # Regular sitemap — <loc>s are page URLs
    return urls[:_MAX_URLS_PER_SITEMAP]


# =============================================================================
# URL filtering — keep only pages under the docs subtree, honor language scope
# =============================================================================
def _filter_urls(
    urls: list[str],
    cfg: DocsIngestionConfig) -> list[str]:
    """
    Apply the same filters Tier 4 uses: blocklist, language path-filter,
    same-host-and-subtree, user-provided extra allow/deny patterns.
    """
    parsed = urlparse(cfg.docs_url)
    target_host = (parsed.netloc or "").lower()
    docs_path = (parsed.path or "/").rstrip("/")

    allow, deny = _build_language_filter(cfg.language)
    # Extra patterns from cfg
    allow.extend(cfg.extra_allow_patterns)
    deny.extend(cfg.extra_deny_patterns)

    polyglot = _is_polyglot_framework(cfg.framework)

    kept: list[str] = []
    for u in urls:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        if host != target_host:
            continue
        path = p.path or "/"
        # Must be under the docs subtree when docs_url has a meaningful path.
        if docs_path and not path.startswith(docs_path):
            continue
        # Polyglot + language-scoped filter
        if polyglot and cfg.language:
            if not _should_keep(u, allow, deny):
                continue
        # User-supplied extra allow/deny still apply for non-polyglot sites
        elif allow or deny:
            if not _should_keep(u, allow, deny):
                continue
        kept.append(u)
    return kept


# =============================================================================
# trafilatura extraction
# =============================================================================
# Extractor rationale (2026-04-21): we'd prefer rs-trafilatura (Rust via
# PyO3, F1 0.859-0.966 on docs) but upstream only ships cp312 wheels as of
# 0.1.1 — our Dockerfile runs Python 3.13 so uv source-builds fail for
# lack of Rust+gcc. Pure-Python trafilatura 2.0 is the fallback (F1 ≈
# 0.791 on docs; still produces usable markdown). Swap is one line when
# rs-trafilatura publishes cp313 wheels — see Step 5 in the ingestion
# pipeline plan.
def _extract_markdown(html: str, url: str) -> Optional[str]:
    """
    Extract main content from HTML as markdown. Returns None on empty
    extraction or import failure (caller treats as a soft-fail, logs and
    skips the page).
    """
    try:
        import trafilatura
    except ImportError:
        logger.warning(
            "[tier-3] trafilatura not installed — falling back to raw "
            "body. Install via `uv pip install trafilatura`."
        )
        return html if html.strip() else None
    try:
        md = trafilatura.extract(
            html,
            output_format = "markdown",
            url = url,
            include_comments = False,
            include_tables = True,
            favor_precision = False,
            favor_recall = True,    # docs pages — grab as much content as possible
        )
    except Exception as e:
        logger.warning(f"[tier-3] trafilatura extract failed for {url}: {e}")
        return None
    if not md or not md.strip():
        return None
    return md


# =============================================================================
# Public entry point
# =============================================================================
async def ingest_sitemap_httpx(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage) -> IngestResult:
    """
    Tier 3 ingestion. Called by the dispatcher when `cfg.tier == 3`.
    Never raises on partial failure — only when the fail rate exceeds
    _MIN_OK_RATIO, which lets the dispatcher fall back to Tier 4 for a
    retry on the genuinely stuck sites.
    """
    parsed = urlparse(cfg.docs_url)
    host = (parsed.netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 3: cannot parse host from docs_url={cfg.docs_url!r}")
    host_root = f"{parsed.scheme}://{parsed.netloc}"

    logger.info(
        f"[tier-3] start framework={cfg.framework!r} host={host} "
        f"docs_url={cfg.docs_url!r} language={cfg.language!r}"
    )

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(_HTTP_TIMEOUT, connect = 10.0),
        follow_redirects = True,
    ) as client:
        # -----------------------------------------------------------------
        # Step 1 — Expand sitemap (recursive sitemapindex unwrap)
        # -----------------------------------------------------------------
        sitemap_url = f"{host_root}/sitemap.xml"
        all_urls = await _expand_sitemap(client, sitemap_url)
        logger.info(
            f"[tier-3] {sitemap_url}: {len(all_urls)} total URLs after expansion"
        )
        if not all_urls:
            raise RuntimeError(
                f"Tier 3: sitemap expansion for {sitemap_url} yielded 0 URLs. "
                f"Dispatcher will fall back to Tier 4."
            )

        # -----------------------------------------------------------------
        # Step 2 — Filter to docs subtree + language + user patterns
        # -----------------------------------------------------------------
        filtered = _filter_urls(all_urls, cfg)
        logger.info(
            f"[tier-3] pre-filter kept {len(filtered)}/{len(all_urls)} URLs "
            f"(docs subtree + language)"
        )
        if not filtered:
            raise RuntimeError(
                f"Tier 3: 0 URLs after filtering for {cfg.framework!r} "
                f"(docs_url={cfg.docs_url}). Likely the sitemap doesn't "
                f"include the docs subtree. Falling back to Tier 4."
            )
        if len(filtered) > cfg.max_pages:
            logger.info(
                f"[tier-3] capping {len(filtered)} → {cfg.max_pages} "
                f"(cfg.max_pages)"
            )
            filtered = filtered[: cfg.max_pages]

        # -----------------------------------------------------------------
        # Step 3 — Parallel page fetch + extract + write
        # -----------------------------------------------------------------
        progress = IngestProgress(cfg.study_id)
        await progress.start(tier = "sitemap", total = len(filtered))

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        failures: list[tuple[str, str]] = []
        manifest: list[ManifestEntry] = []
        total_bytes = 0
        completed = 0

        async def _one(url: str) -> None:
            nonlocal total_bytes, completed
            async with sem:
                try:
                    resp = await _fetch_text(client, url)
                except Exception as e:
                    failures.append((url, f"{type(e).__name__}: {e}"))
                    completed += 1
                    await progress.update(completed, f"(failed) {url}")
                    return
                if resp.status_code != 200:
                    failures.append((url, f"HTTP {resp.status_code}"))
                    completed += 1
                    await progress.update(completed, f"(failed) {url}")
                    return
                md = _extract_markdown(resp.text, url)
                if not md:
                    failures.append((url, "empty extraction"))
                    completed += 1
                    await progress.update(completed, f"(failed) {url}")
                    return
                slug = _slugify(url)
                entry = await _write_raw(
                    storage = storage,
                    study_root = cfg.study_root,
                    slug = slug,
                    content = md,
                    url = url,
                    tier = "sitemap",
                    cfg = cfg,
                )
                if entry is not None:
                    manifest.append(entry)
                    total_bytes += entry.bytes
                completed += 1
                await progress.update(completed, url)

        try:
            await asyncio.gather(*(_one(u) for u in filtered))
        finally:
            await progress.finish(status = "done" if manifest else "failed")
            await progress.close()

    # -----------------------------------------------------------------
    # Step 4 — Result + partial-failure check
    # -----------------------------------------------------------------
    attempted = len(filtered)
    succeeded = len(manifest)
    fail_count = len(failures)

    if attempted > 0 and succeeded / attempted < _MIN_OK_RATIO:
        logger.error(
            f"[tier-3] ABORT: only {succeeded}/{attempted} pages succeeded "
            f"(below {_MIN_OK_RATIO*100:.0f}% threshold). Failures: "
            f"{failures[:5]}{'...' if fail_count > 5 else ''}"
        )
        raise RuntimeError(
            f"Tier 3 ingestion degraded beyond acceptable threshold "
            f"({succeeded}/{attempted} succeeded). "
            f"Dispatcher will fall back to Tier 4 Playwright."
        )
    if fail_count:
        logger.warning(
            f"[tier-3] {fail_count}/{attempted} page failures (continuing). "
            f"Samples: {failures[:5]}"
        )

    logger.info(
        f"[tier-3] OK — {succeeded} files, {total_bytes} bytes "
        f"({fail_count} failures)"
    )
    return IngestResult(
        tier_used = "sitemap",
        total_files = succeeded,
        total_bytes = total_bytes,
        manifest = manifest,
        skipped_urls = [u for u, _ in failures],
    )
