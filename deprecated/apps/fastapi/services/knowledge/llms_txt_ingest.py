"""
Knowledge Distiller — Tier 2 Ingestion (llms.txt index + parallel link fetch)

Dispatched by `services/knowledge/ingestion.py` when the resolver assigned
`tier == 2` — meaning sources.yaml has a curated `llms.txt` URL for this
framework but no `llms-full.txt`.

PIPELINE (~30-90s for typical 20-100 link indexes, vs ~20 min Tier 4 Playwright):
  1. GET `cfg.docs_url` (the curated llms.txt URL) directly — no construction
  2. Parse via the official AnswerDotAI `llms_txt` PyPI parser — returns
     .title / .summary / .sections{name: [{title, url, desc}]}
  3. Collect all link URLs across every section
  4. Filter to http(s) + under the llms.txt URL's directory (subtree)
  5. Parallel fetch each URL with Semaphore(10) + tenacity retries:
     - URLs ending in .md / .mdx / .markdown → save raw body (already MD)
     - URLs ending in anything else (HTML) → Crawl4AI markdown extract
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
import time
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

# Format-priority dedup for llms.txt URL lists. When a publisher exposes
# the same logical page under multiple URL forms (Docker lists every page
# as both `/foo` and `/foo.md`), we keep one URL per (host, path-stem)
# group — the one with the lowest priority number below. Raw markdown
# wins because it's byte-stable, structurally faithful, and chrome-free;
# HTML/no-ext requires Crawl4AI extraction and may leak widget chrome
# (Docker's "Gordon" assistant being the canonical case).
# Extensions not listed are treated as opaque path segments — `.txt` and
# `.json` and similar are NOT format siblings of `/foo`, they're their
# own logical page.
_FORMAT_PRIORITY: dict[str, int] = {
    ".md": 0,
    ".mdx": 0,
    ".markdown": 0,
    ".html": 2,
    ".htm": 2,
    "": 3,  # bare path — treat as rendered HTML
}

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
# Format-priority dedup
# =============================================================================
def _path_stem_and_ext(url: str) -> tuple[str, str, str]:
    """
    Decompose a URL into (host, path-stem, extension).

    `path-stem` strips the trailing slash and any extension recognized
    in `_FORMAT_PRIORITY`. So `/foo`, `/foo/`, `/foo.html`, and
    `/foo.md` all share the stem `/foo` on the same host. Unrecognized
    extensions (e.g., `.txt`, `.json`, `.tar.gz`) are kept as part of
    the stem so we don't accidentally collapse unrelated pages.
    """
    p = urlparse(url)
    host = (p.netloc or "").lower()
    path = (p.path or "").rstrip("/")
    if "/" in path:
        head, last = path.rsplit("/", 1)
    else:
        head, last = "", path
    if "." in last:
        stem_segment, _, ext = last.rpartition(".")
        ext = "." + ext.lower()
    else:
        stem_segment, ext = last, ""
    if ext not in _FORMAT_PRIORITY:
        # Unknown extension — keep the whole `last` as the stem so
        # `/foo.json` is its own page, not a `.json` sibling of `/foo`.
        stem_segment, ext = last, ""
    full_stem = f"{head}/{stem_segment}" if head else stem_segment
    return host, full_stem, ext


def _prefer_best_format(urls: list[str]) -> list[str]:
    """
    Group input URLs by (host, path-stem); within each group keep the
    URL whose format has the lowest priority number (= highest quality
    per `_FORMAT_PRIORITY`). Order-preserving: each group occupies its
    first-seen position in the output, but the URL emitted is the best
    one in that group.

    Idempotent and a no-op for groups of size 1 — only fires when the
    publisher actually lists multiple format URLs for the same logical
    page (Docker case). NVIDIA, Mintlify, Terragrunt etc. pass through
    unchanged.
    """
    best: dict[tuple[str, str], tuple[int, str]] = {}
    for u in urls:
        host, stem, ext = _path_stem_and_ext(u)
        prio = _FORMAT_PRIORITY.get(ext, 4)  # safety net for unrecognized
        gk = (host, stem)
        if gk not in best or prio < best[gk][0]:
            best[gk] = (prio, u)

    seen: set[tuple[str, str]] = set()
    out: list[str] = []
    for u in urls:
        host, stem, _ = _path_stem_and_ext(u)
        gk = (host, stem)
        if gk in seen:
            continue
        seen.add(gk)
        out.append(best[gk][1])
    return out


# =============================================================================
# URL filtering (same semantics as Tier 3)
# =============================================================================
def _filter_urls(
    urls: list[str],
    cfg: DocsIngestionConfig) -> list[str]:
    """Keep URLs under docs_url + honor language scope + allow/deny patterns."""
    parsed = urlparse(cfg.docs_url)
    target_host = (parsed.netloc or "").lower()
    # cfg.docs_url is the curated llms.txt URL — the docs subtree is the
    # directory containing it (strip filename). For host-root files the
    # subtree collapses to "/" → matches everything on the host.
    raw_path = parsed.path or "/"
    docs_path = raw_path.rsplit("/", 1)[0] if "/" in raw_path else ""
    docs_path = docs_path or "/"
    if docs_path == "/":
        docs_path = ""  # treat as no path constraint

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
    """
    HTML → markdown via Crawl4AI's PruningContentFilter +
    DefaultMarkdownGenerator (configured for code-preservation).

    See services/knowledge/markdown_extractor.py for the singleton
    converter and docs/KNOWLEDGE-DISTILLER-MARKDOWN-EXTRACTOR-MIGRATION.md
    for the rationale (replaces trafilatura, 2026-04-28).
    """
    from services.knowledge.markdown_extractor import html_to_markdown
    return html_to_markdown(html, url)


# =============================================================================
# Public entry point
# =============================================================================
async def ingest_llms_txt(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage) -> IngestResult:
    """
    Tier 2 ingestion. Called by the dispatcher when `cfg.tier == 2`.
    Fetches `cfg.docs_url` directly — the resolver supplies the exact
    llms.txt URL from sources.yaml; no construction. Raises RuntimeError
    on total failure so the dispatcher can fall back to Tier 4 Playwright.
    """
    llms_txt_url = cfg.docs_url
    parsed = urlparse(llms_txt_url)
    host = (parsed.netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 2: cannot parse host from docs_url={llms_txt_url!r}")

    logger.info(
        f"[tier-2] start framework={cfg.framework!r} url={llms_txt_url} "
        f"language={cfg.language!r}"
    )

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(_HTTP_TIMEOUT, connect = 10.0),
        follow_redirects = True,
    ) as client:
        try:
            resp = await _fetch(client, llms_txt_url)
        except Exception as e:
            raise RuntimeError(
                f"Tier 2 fetch failed for {cfg.framework!r} at {llms_txt_url}: "
                f"{type(e).__name__}: {e}"
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Tier 2: {llms_txt_url} → HTTP {resp.status_code}"
            )
        llms_body = resp.text
        if len(llms_body) < 50:
            raise RuntimeError(
                f"Tier 2: {llms_txt_url} body too short ({len(llms_body)} bytes)"
            )
        logger.info(f"[tier-2] using llms.txt at {llms_txt_url}")

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

        # Per-page format dedup — keep `.md` over HTML/no-ext when the
        # publisher lists both for the same logical page. No-op for
        # publishers without duplicates.
        before = len(filtered)
        filtered = _prefer_best_format(filtered)
        if len(filtered) != before:
            logger.info(
                f"[tier-2] format-priority dedup kept {len(filtered)}/{before} "
                f"URLs (dropped {before - len(filtered)} HTML twins of .md siblings)"
            )

        # -----------------------------------------------------------------
        # Step 3 — Parallel fetch + extract + write
        # -----------------------------------------------------------------
        progress = IngestProgress(cfg.study_id)
        await progress.start(tier = "llms_txt", total = len(filtered))

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        failures: list[tuple[str, str]] = []
        # Pre-allocated by URL position so manifest order tracks the parsed
        # llms.txt order (the publisher's curated reading sequence) instead
        # of fetch-completion order under the parallel Semaphore. The
        # zero-padded ordinal in each slug then makes alphabetical
        # filesystem listing equal that same order — same rule applied in
        # post_ingest.split_monolith_if_needed for Tier 1.
        width = max(4, len(str(max(0, len(filtered) - 1))))
        entries: list[Optional[ManifestEntry]] = [None] * len(filtered)
        total_bytes = 0
        completed = 0

        async def _one(i: int, url: str) -> None:
            nonlocal total_bytes, completed
            async with sem:
                t0 = time.monotonic()
                try:
                    r = await _fetch(client, url)
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    failures.append((url, err))
                    completed += 1
                    await progress.update(completed, f"(failed) {url}")
                    await progress.record_url(
                        url, status="fetch_error", tier="llms_txt",
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
                        url, status="http_error", tier="llms_txt",
                        http_code=r.status_code, fetch_ms=fetch_ms,
                        bytes_fetched=len(r.text or ""),
                        error_msg=f"HTTP {r.status_code}",
                    )
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
                    await progress.record_url(
                        url, status="extract_empty", tier="llms_txt",
                        http_code=r.status_code, fetch_ms=fetch_ms,
                        bytes_fetched=len(body or ""), extracted_chars=0,
                        error_msg="empty / unparseable content",
                    )
                    return
                slug = f"{i:0{width}d}-{_slugify(url)}"
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
                    entries[i] = entry
                    total_bytes += entry.bytes
                completed += 1
                await progress.update(completed, url)
                await progress.record_url(
                    url, status="success", tier="llms_txt",
                    http_code=r.status_code, fetch_ms=fetch_ms,
                    bytes_fetched=len(body or ""), extracted_chars=len(content),
                )

        try:
            await asyncio.gather(*(_one(i, u) for i, u in enumerate(filtered)))
        finally:
            await progress.finish(status = "done" if any(entries) else "failed")
            await progress.close()

    # Preserve submission order; drop slots that failed (None).
    manifest: list[ManifestEntry] = [e for e in entries if e is not None]

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
