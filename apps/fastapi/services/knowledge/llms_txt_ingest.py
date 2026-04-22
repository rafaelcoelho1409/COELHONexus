"""
Knowledge Distiller — Tier 2 Ingestion (llms.txt index + parallel link fetch)

Dispatched by `services/knowledge/ingestion.py` when the resolver assigned
`tier == 2` — meaning Stage D's content-validated probe confirmed the site
publishes a real `/llms.txt` (markdown headings and/or .md URL references)
but no `/llms-full.txt`.

PIPELINE (~30-90s for typical 20-100 link indexes, vs ~20 min Tier 4 Playwright):
  1. GET host-root `/llms.txt` (resolver confirmed VALID)
  2. Parse via the official AnswerDotAI `llms_txt` PyPI parser — returns
     .title / .summary / .sections{name: [{title, url, desc}]}
  3. Collect all link URLs across every section
  4. Filter to http(s) + under docs_url subtree (reuse Tier 3 filters)
  5. Parallel fetch each URL with Semaphore(10) + tenacity retries:
     - URLs ending in .md / .mdx / .markdown → save raw body (already MD)
     - URLs ending in anything else (HTML) → trafilatura extract → MD
  6. `_write_raw()` each result to MinIO
  7. Partial-failure policy: abort if <50% succeed → dispatcher falls back

WHY llms.txt BEATS SITEMAP FOR Tier 2 SITES:
  Publishers who bother creating llms.txt CURATE the set of URLs worth
  reading (unlike sitemap.xml which dumps everything). So an llms.txt
  index is both smaller AND higher quality than the equivalent sitemap
  subset — fewer pages fetched, less noise to drop downstream.

ADOPTION REALITY:
  llms.txt adoption was ~0.011% of websites as of May 2025. Tier 2 is
  rare in practice — NVIDIA docs, llms-central registry sites, some
  AI tools. Most frameworks end up Tier 3 or Tier 4. Implementing this
  anyway for the ones that do use it (NVIDIA specifically).

Reference: https://llmstxt.org/   AnswerDotAI/llms-txt (GitHub)
docs/KNOWLEDGE-DISTILLER-INGESTION-PIPELINE-PLAN.md §Step 6
"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse, urljoin
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
    _should_keep,
    _slugify,
    _write_raw,
)
from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================
_USER_AGENT = "COELHONexus-KD-Tier2/1.0"
_HTTP_TIMEOUT = 30.0
_MAX_CONCURRENT = 10          # parallel page fetches — polite single-host load
_MIN_OK_RATIO = 0.5           # abort tier if <50% fetched successfully

# File extensions that are already markdown (skip HTML extraction)
_MD_EXTS = (".md", ".mdx", ".markdown")

# Regex fallback when AnswerDotAI's `parse_llms_file` fails or returns 0
# URLs. NVIDIA's llms.txt at docs.nvidia.com (as of April 2026) mixes bare
# URLs with markdown-link syntax per-section, which breaks the official
# parser. Bare-URL scraping is robust to that + to any other hand-rolled
# format variations we might encounter in the wild.
#
# Captures EITHER `](url)` markdown link target OR bare `http(s)://…` URLs.
_URL_RE = re.compile(
    r"\]\((https?://[^\s)]+)\)|(?<![\w/:\-])(https?://[^\s<>()\"']+)",
    re.IGNORECASE,
)


# =============================================================================
# HTTP helpers
# =============================================================================
@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 15),
)
async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": _USER_AGENT})


# =============================================================================
# Parse llms.txt → list of candidate URLs
# =============================================================================
def _dedupe_absolute(urls: list[str], base_url: str) -> list[str]:
    """Resolve relative URLs + dedupe while preserving first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        if not raw:
            continue
        full = raw if raw.startswith(("http://", "https://")) else urljoin(base_url, raw)
        if not full.startswith(("http://", "https://")):
            continue
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


def _parse_llms_txt(body: str, base_url: str) -> list[str]:
    """
    Extract candidate URLs from an llms.txt body. Tries the official
    AnswerDotAI parser first (structured link extraction); falls back
    to a regex scan when it raises or returns 0 URLs.

    NVIDIA's docs.nvidia.com/llms.txt (April 2026) is the motivating
    case for the fallback — it mixes bare URLs and markdown-link
    syntax in ways the official parser doesn't accept.
    """
    # ----- Primary: AnswerDotAI parser (structured) -----
    library_urls: list[str] = []
    try:
        from llms_txt import parse_llms_file
    except ImportError:
        logger.info("[tier-2] llms_txt package unavailable, using regex fallback")
    else:
        try:
            parsed = parse_llms_file(body)
            sections = getattr(parsed, "sections", {}) or {}
            for _section_name, links in sections.items():
                for link in (links or []):
                    raw = (
                        link.get("url") if isinstance(link, dict)
                        else getattr(link, "url", None)
                    ) or ""
                    if raw:
                        library_urls.append(raw)
        except Exception as e:
            logger.info(
                f"[tier-2] parse_llms_file failed ({e}); using regex fallback"
            )

    deduped = _dedupe_absolute(library_urls, base_url)
    if deduped:
        return deduped

    # ----- Fallback: regex URL scrape -----
    # Captures both markdown-link targets `](url)` and bare `http(s)://…`.
    regex_urls: list[str] = []
    for match in _URL_RE.finditer(body):
        # Group 1 = markdown-link target, Group 2 = bare URL
        url = (match.group(1) or match.group(2) or "").strip().rstrip(".,;:)")
        if url:
            regex_urls.append(url)
    deduped = _dedupe_absolute(regex_urls, base_url)
    logger.info(
        f"[tier-2] regex fallback extracted {len(deduped)} URLs from llms.txt"
    )
    return deduped


# =============================================================================
# URL filtering (same semantics as Tier 3)
# =============================================================================
def _filter_urls(
    urls: list[str],
    cfg: DocsIngestionConfig) -> list[str]:
    """Keep URLs under docs_url + honor language scope + allow/deny patterns."""
    parsed = urlparse(cfg.docs_url)
    target_host = (parsed.netloc or "").lower()
    docs_path = (parsed.path or "/").rstrip("/")

    allow, deny = _build_language_filter(cfg.language)
    allow.extend(cfg.extra_allow_patterns)
    deny.extend(cfg.extra_deny_patterns)
    polyglot = _is_polyglot_framework(cfg.framework)

    kept: list[str] = []
    for u in urls:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        # Permissive host-check: allow exact match OR a sibling subdomain
        # (e.g., `docs.nvidia.com` → accept `developer.nvidia.com` links too).
        # llms.txt sometimes links off-subdomain to official related sites.
        if host != target_host:
            # Compute registrable-domain match (2-part suffix)
            target_rdn = ".".join(target_host.split(".")[-2:]) if target_host else ""
            host_rdn = ".".join(host.split(".")[-2:]) if host else ""
            if not (target_rdn and host_rdn and target_rdn == host_rdn):
                continue
        path = p.path or "/"
        # When docs_url pins a subtree, enforce it ONLY for the exact host.
        # Sibling-subdomain links pass regardless of path (the subtree is
        # specific to the primary host's path layout).
        if docs_path and host == target_host and not path.startswith(docs_path):
            continue
        if polyglot and cfg.language:
            if not _should_keep(u, allow, deny):
                continue
        elif allow or deny:
            if not _should_keep(u, allow, deny):
                continue
        kept.append(u)
    return kept


# =============================================================================
# Per-link fetch + extract
# =============================================================================
def _looks_like_markdown(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return any(path.endswith(ext) for ext in _MD_EXTS)


def _html_to_markdown(html: str, url: str) -> Optional[str]:
    """Trafilatura extraction — same path as Tier 3. Swap to rs-trafilatura
    when cp313 wheels are available (see sitemap_ingest.py)."""
    try:
        import trafilatura
    except ImportError:
        return html if html.strip() else None
    try:
        md = trafilatura.extract(
            html,
            output_format = "markdown",
            url = url,
            include_comments = False,
            include_tables = True,
            favor_recall = True,
        )
    except Exception as e:
        logger.warning(f"[tier-2] trafilatura failed for {url}: {e}")
        return None
    return md if (md and md.strip()) else None


# =============================================================================
# Public entry point
# =============================================================================
async def ingest_llms_txt(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage) -> IngestResult:
    """
    Tier 2 ingestion. Called by the dispatcher when `cfg.tier == 2`.
    Raises RuntimeError on total failure so the dispatcher can fall back
    to Tier 4 Playwright.
    """
    parsed = urlparse(cfg.docs_url)
    host = (parsed.netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 2: cannot parse host from docs_url={cfg.docs_url!r}")
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    deep_base = cfg.docs_url.rstrip("/")

    logger.info(
        f"[tier-2] start framework={cfg.framework!r} host={host} "
        f"language={cfg.language!r}"
    )

    # -----------------------------------------------------------------
    # Step 1 — Fetch + parse llms.txt
    # -----------------------------------------------------------------
    # Try host-root first (llmstxt.org canonical location), then fall back
    # to the deep docs_url path. Examples of deep-path publishers in the
    # wild: www.fastht.ml/docs/llms.txt, some MkDocs-material projects.
    candidates = [f"{host_root}/llms.txt"]
    if deep_base != host_root.rstrip("/"):
        candidates.append(f"{deep_base}/llms.txt")

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(_HTTP_TIMEOUT, connect = 10.0),
        follow_redirects = True,
    ) as client:
        llms_txt_url: str | None = None
        llms_body: str | None = None
        last_error: str | None = None
        for candidate in candidates:
            try:
                resp = await _fetch(client, candidate)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.info(f"[tier-2] {candidate} fetch failed: {last_error}")
                continue
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                logger.info(f"[tier-2] {candidate} → {last_error}")
                continue
            body = resp.text
            if len(body) < 50:
                last_error = f"body too short ({len(body)} bytes)"
                logger.info(f"[tier-2] {candidate} → {last_error}")
                continue
            llms_txt_url = candidate
            llms_body = body
            logger.info(f"[tier-2] using llms.txt at {candidate}")
            break
        if llms_body is None or llms_txt_url is None:
            raise RuntimeError(
                f"Tier 2: no reachable llms.txt across {candidates}; "
                f"last error: {last_error}"
            )

        # Parse — relative URLs resolved against the llms.txt URL itself
        # (per the spec, relative links are relative to the document).
        all_urls = _parse_llms_txt(llms_body, llms_txt_url)
        logger.info(
            f"[tier-2] {llms_txt_url}: parsed {len(all_urls)} URLs across sections"
        )
        if not all_urls:
            raise RuntimeError(
                f"Tier 2: zero URLs extracted from {llms_txt_url}. "
                f"Dispatcher will fall back to Tier 4."
            )

        # -----------------------------------------------------------------
        # Step 2 — Filter to docs subtree + language + user patterns
        # -----------------------------------------------------------------
        filtered = _filter_urls(all_urls, cfg)
        logger.info(
            f"[tier-2] pre-filter kept {len(filtered)}/{len(all_urls)} URLs"
        )
        if not filtered:
            raise RuntimeError(
                f"Tier 2: 0 URLs after filtering. llms.txt for {cfg.framework!r} "
                f"may link exclusively off-subtree — falling back to Tier 4."
            )
        if len(filtered) > cfg.max_pages:
            logger.info(
                f"[tier-2] capping {len(filtered)} → {cfg.max_pages} (cfg.max_pages)"
            )
            filtered = filtered[: cfg.max_pages]

        # -----------------------------------------------------------------
        # Step 3 — Parallel fetch + extract + write
        # -----------------------------------------------------------------
        progress = IngestProgress(cfg.study_id)
        await progress.start(tier = "llms_txt", total = len(filtered))

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        failures: list[tuple[str, str]] = []
        manifest: list[ManifestEntry] = []
        total_bytes = 0
        completed = 0

        async def _one(url: str) -> None:
            nonlocal total_bytes, completed
            async with sem:
                try:
                    r = await _fetch(client, url)
                except Exception as e:
                    failures.append((url, f"{type(e).__name__}: {e}"))
                    completed += 1
                    await progress.update(completed, f"(failed) {url}")
                    return
                if r.status_code != 200:
                    failures.append((url, f"HTTP {r.status_code}"))
                    completed += 1
                    await progress.update(completed, f"(failed) {url}")
                    return
                body = r.text
                if _looks_like_markdown(url):
                    content = body if body.strip() else None
                else:
                    content = _html_to_markdown(body, url)
                if not content:
                    failures.append((url, "empty / unparseable content"))
                    completed += 1
                    await progress.update(completed, f"(failed) {url}")
                    return
                slug = _slugify(url)
                entry = await _write_raw(
                    storage = storage,
                    study_root = cfg.study_root,
                    slug = slug,
                    content = content,
                    url = url,
                    tier = "llms_txt",
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
            f"[tier-2] ABORT: only {succeeded}/{attempted} pages succeeded "
            f"(below {_MIN_OK_RATIO*100:.0f}% threshold). "
            f"Samples: {failures[:5]}"
        )
        raise RuntimeError(
            f"Tier 2 ingestion degraded beyond acceptable threshold "
            f"({succeeded}/{attempted} succeeded). Dispatcher will fall "
            f"back to Tier 4 Playwright."
        )
    if fail_count:
        logger.warning(
            f"[tier-2] {fail_count}/{attempted} page failures (continuing). "
            f"Samples: {failures[:5]}"
        )

    logger.info(
        f"[tier-2] OK — {succeeded} files, {total_bytes} bytes "
        f"({fail_count} failures)"
    )
    return IngestResult(
        tier_used = "llms_txt",
        total_files = succeeded,
        total_bytes = total_bytes,
        manifest = manifest,
        skipped_urls = [u for u, _ in failures],
    )
