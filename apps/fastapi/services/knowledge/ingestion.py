"""
Knowledge Distiller — Ingestion Dispatcher + Tier 4 Crawl4AI

This module exposes the public `ingest_framework_docs()` entry point and
dispatches to one of five tier-specific strategies based on `cfg.tier` and
`cfg.github_discover` (both set by the resolver in Stage D):

  Tier-GH (github readme_only)  services/knowledge/github_ingest.py
  Tier 1  (llms-full.txt)       services/knowledge/llms_full_ingest.py
  Tier 2  (llms.txt)            services/knowledge/llms_txt_ingest.py
  Tier 3  (sitemap.xml)         services/knowledge/sitemap_ingest.py
  Tier 4  (Playwright/Crawl4AI) `_ingest_crawl4ai()` below — the default
                                fallback when tier is None, when higher
                                tiers raise, or when readme_only discovery
                                returns no markdown.

TIER 4 PIPELINE (Crawl4AI-powered fallback path in this module):

  1. AsyncUrlSeeder discovers URLs from `/sitemap.xml` + Common Crawl index
  2. URL pre-filter (blocklist, non-target-language paths, same-host only)
  3. AsyncWebCrawler.arun_many() fetches each via Playwright Chromium
  4. Each page goes through a minimal post-fetch empty-content gate
     (PruningContentFilter has already removed nav/sidebar upstream)
  5. Write successful markdown to MinIO at `<study_root>/research/raw/`

Crawl4AI via Playwright is the right last-resort because it passes WAF bot
checks (real browser fingerprint) and renders JS SPAs (Next.js, React
Router, Vue) correctly. Higher tiers use plain httpx and skip the browser
when the site publishes a structured index.

LANGUAGE SCOPING
Polyglot docs (OpenTelemetry, Kubernetes, gRPC, ...) publish materials for
many programming languages at once. When the user asks for a specific
language (signal from the scope classifier), we filter URLs to that
language's paths plus language-agnostic pages (concepts, specification).
The same filter helpers (`_build_language_filter`, `_is_polyglot_framework`,
`_should_keep`) are re-used by Tier 2/3 for uniform behavior.

SHARED HELPERS EXPORTED TO OTHER TIERS
  `_write_raw`, `_slugify`, `_build_language_filter`, `_is_polyglot_framework`,
  `_should_keep`, `_matches_any` are imported by Tier 2/3/GH.

References:
  - docs/KNOWLEDGE-DISTILLER-ARCHITECTURE.md (crawl layer)
  - docs/KNOWLEDGE-DISTILLER-INGESTION-PIPELINE-PLAN.md (tier design)
  - Crawl4AI v0.8 API: AsyncUrlSeeder + SeedingConfig + AsyncWebCrawler
"""
import asyncio
import fnmatch
import json
import logging
import re
import ssl
from typing import Optional
from urllib.parse import urlparse
from urllib.request import urlopen


from schemas.knowledge.ingestion import (
    DocsIngestionConfig,
    ManifestEntry,
    IngestResult
)
from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


# =============================================================================
# Constants — scope heuristics
# =============================================================================

# Frameworks whose docs ship a polyglot /llms-full.txt (content covers many
# programming languages). When the user requests a specific language on one
# of these, we skip Tier 1 because a concatenated blob can't be path-filtered.
# Intentionally conservative — add entries as new polyglot frameworks surface.
POLYGLOT_FRAMEWORKS: set[str] = {
    "opentelemetry",
    "grpc",
    "protobuf",
    "protocol buffers",
    "kubernetes",
    "prometheus",
    "apache kafka",
    "kafka",
    "rabbitmq",
    "elastic",
    "elasticsearch",
    "pulsar",
    "etcd",
}

# Programming language → URL path slug aliases. Used to both INCLUDE the target
# language's paths and EXCLUDE all other languages' paths.
LANGUAGE_PATH_MAP: dict[str, list[str]] = {
    "python":     ["python", "py"],
    "javascript": ["javascript", "js", "nodejs", "node"],
    "typescript": ["typescript", "ts"],
    "go":         ["go", "golang"],
    "rust":       ["rust", "rs"],
    "java":       ["java"],
    "kotlin":     ["kotlin", "kt"],
    "csharp":     ["csharp", "cs", "dotnet", "net"],
    "ruby":       ["ruby", "rb"],
    "php":        ["php"],
    "swift":      ["swift"],
    "cpp":        ["cpp", "c-plus-plus", "c++"],
    "c":          ["c-lang"],  # avoid "c" alone — matches too much
    "elixir":     ["elixir", "ex"],
    "erlang":     ["erlang"],
    "scala":      ["scala"],
    "haskell":    ["haskell", "hs"],
}

DEFAULT_ALLOW_PATTERNS: list[str] = [
    "*docs*",
    "*guide*",
    "*tutorial*",
    "*api*",
    "*reference*",
    "*quickstart*",
    "*getting-started*",
    "*concepts*",
    "*specification*",
    "*overview*",
]

DEFAULT_DENY_PATTERNS: list[str] = [
    # --- Release churn (noise for learners) ---
    "*/blog/*", "*/news/*", "*/posts/*", "*/announcements/*",
    "*/changelog/*", "*/changelogs/*", "*/releases/*", "*/release-notes/*",
    "*/whats-new/*", "*/history/*",
    # --- Marketing / business ---
    "*/pricing/*", "*/jobs/*", "*/careers/*", "*/contact/*",
    "*/case-studies/*", "*/customers/*", "*/events/*", "*/webinar*",
    "*/newsletter/*", "*/partners/*", "*/solutions/*", "*/products/*",
    "*/enterprise/*",
    # --- Legal / governance boilerplate ---
    "*/legal/*", "*/privacy/*", "*/terms/*", "*/cookie*",
    "*/trademark*", "*/license*/*", "*/lics/*",
    "*/about/*", "*/team/*", "*/sponsors/*", "*/governance/*",
    # --- Contributor / community pages (not for learners) ---
    "*/contributing/*", "*/contribute/*", "*/code-of-conduct/*",
    "*/security-policy/*", "*/security/*", "*/community/*",
    "*/forum/*", "*/discuss/*", "*/gallery/*", "*/showcase/*",
    # --- Stale / archived content ---
    "*/archive/*", "*/archives/*", "*/legacy/*", "*/old/*",
    "*/deprecated/*",
    # --- Navigation / indexes (auto-generated by Sphinx, mkdocs-material, etc.) ---
    "*/search.html", "*/genindex*", "*/py-modindex*",
    "*/tag/*", "*/tags/*", "*/categories/*",
    # --- Non-HTML assets ---
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.tgz",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp",
    "*.mp4", "*.mov", "*.webm",
]

# Regex fragments that, when matched in a URL PATH, signal a non-target-language
# localization directory (e.g. /zh/, /ja/, /pt-BR/). Used when the study's
# target language is English (or not specified = default English-biased).
NON_TARGET_LANGUAGE_PATH_RE = re.compile(
    r"/(zh|cn|ja|ko|pt|fr|de|es|ru|it|tr|pl|nl|vi|th|ar|id|hi)(-[a-z]{2})?(/|$)",
    re.IGNORECASE,
)

# Tier 4 BFS keyword scorer — boosts relevance of these terms in URLs/content
DEFAULT_SCORER_KEYWORDS: list[str] = [
    "guide",
    "tutorial",
    "documentation",
    "api",
    "reference",
    "quickstart",
    "example",
]


# =============================================================================
# Helpers
# =============================================================================
def _resolve_cdp_ws_url(cdp_endpoint: str) -> Optional[str]:
    """
    Given an HTTP(S) Playwright CDP server URL (e.g.
    `https://playwright-cdp-headless.YOUR_TAILNET_DOMAIN.ts.net`), hit its
    `/json/version` endpoint to discover the WebSocket debugger URL and
    rewrite the scheme + host so we actually connect through the same
    ingress we reached HTTP on.

    Mirrors the pattern in routers/v1/youtube/helpers.py::_get_cdp_websocket_url
    so both code paths behave identically.

    Returns the wss://…/devtools/browser/<id> URL, or None on any failure
    (caller should fall back to launching a local Chromium via
    `playwright install chromium`).
    """
    if not cdp_endpoint:
        return None
    parsed = urlparse(cdp_endpoint)
    json_url = f"{cdp_endpoint.rstrip('/')}/json/version"
    try:
        ctx = ssl.create_default_context()
        # Tailscale ingress sometimes uses internal certs — relax verification.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urlopen(json_url, timeout = 10, context = ctx) as resp:
            data = json.loads(resp.read().decode())
            ws_url = data.get("webSocketDebuggerUrl", "")
            if not ws_url:
                logger.warning(f"[ingest][cdp] no webSocketDebuggerUrl at {json_url}")
                return None
            ws_path = urlparse(ws_url).path
            scheme = "wss" if parsed.scheme == "https" else "ws"
            return f"{scheme}://{parsed.netloc}{ws_path}"
    except Exception as e:
        logger.warning(f"[ingest][cdp] resolve failed for {cdp_endpoint}: {e}")
        return None


def _build_language_filter(language: Optional[str]) -> tuple[list[str], list[str]]:
    """
    Produce (allow_patterns, deny_patterns) given an optional language hint.

    Rules:
      - Always keep language-agnostic content (concepts, specification, ...)
      - If language given: allow ONLY that language's path slugs + agnostic
      - If language given: deny every OTHER language's slugs
      - If no language: use conservative defaults
    """
    if not language:
        return (list(DEFAULT_ALLOW_PATTERNS), list(DEFAULT_DENY_PATTERNS))
    key = language.strip().lower()
    target_slugs = LANGUAGE_PATH_MAP.get(key, [key])
    other_slugs = [
        slug
        for k, slugs in LANGUAGE_PATH_MAP.items()
        if k != key
        for slug in slugs
        if len(slug) > 2     # drop 2-char slugs ("js", "go") from deny list to avoid false positives
    ]
    allow = [
        *DEFAULT_ALLOW_PATTERNS,
        *[f"*/languages/{s}/*" for s in target_slugs],
        *[f"*/{s}/*" for s in target_slugs if len(s) > 2],
    ]
    deny = [
        *DEFAULT_DENY_PATTERNS,
        *[f"*/languages/{s}/*" for s in other_slugs],
        *[f"*/{s}/*" for s in other_slugs],
    ]
    return allow, deny


def _is_polyglot_framework(framework: str) -> bool:
    """Does this framework publish polyglot docs (e.g. OpenTelemetry)?"""
    key = framework.strip().lower()
    return any(p in key or key in p for p in POLYGLOT_FRAMEWORKS)


def _matches_any(url: str, patterns: list[str]) -> bool:
    """True if URL matches any of the fnmatch-style patterns."""
    return any(fnmatch.fnmatch(url, p) for p in patterns)


def _should_keep(url: str, allow: list[str], deny: list[str]) -> bool:
    """Apply allow/deny patterns. Deny wins when both match."""
    if _matches_any(url, deny):
        return False
    if not allow:
        return True
    return _matches_any(url, allow)


def _slugify(url: str) -> str:
    """
    URL → safe key segment.
    Example: 'https://docs.x.com/api/auth/oauth?v=1' → 'docs-x-com-api-auth-oauth'
    """
    parsed = urlparse(url)
    parts = [parsed.path.strip("/")]
    raw = "-".join(p for p in parts if p)
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if not slug:
        slug = "index"
    # Prefix with host for uniqueness across subdomains
    host = parsed.netloc.replace(".", "-")
    if host and host not in slug:
        slug = f"{host}-{slug}"
    return slug[:120]  # keep key segments manageable


def _passes_content_quality(
    content: str,
    min_chars: int,
    max_link_text_ratio: float) -> tuple[bool, str]:
    """
    Post-fetch quality gate — now minimal.

    Historical context (pre-2026-04-21): this function had char-count and
    link-ratio heuristics to catch nav pages / stubs. Those heuristics are
    OBSOLETE now that Crawl4AI's `PruningContentFilter` runs upstream and
    removes nav/sidebar/aside tree-wise. What reaches here IS already the
    pruned content.

    Current semantics: only reject empty / whitespace-only content. Every
    non-empty page goes to the corpus. The planner's `unused_files` bucket
    handles semantic noise filtering at much higher quality than a blind
    char count.

    Args kept for signature compatibility with existing callers; values
    are ignored.
    """
    stripped = content.strip()
    if not stripped:
        return False, "empty content"
    return True, ""


async def _write_raw(
    storage: MinIOStudyStorage,
    study_root: str,
    slug: str,
    content: str,
    url: str,
    tier: str,
    cfg: "DocsIngestionConfig | None" = None) -> Optional[ManifestEntry]:
    """
    Write content to MinIO at <study_root>/research/raw/<slug>.md.
    Returns a ManifestEntry on success, None if content failed the quality gate.

    When `cfg` is supplied, applies `min_page_chars` + `max_link_text_ratio`
    filters. Pages that fail are logged-and-dropped, never written.
    """
    if cfg is not None:
        keep, reason = _passes_content_quality(
            content,
            min_chars = cfg.min_page_chars,
            max_link_text_ratio = cfg.max_link_text_ratio,
        )
        if not keep:
            logger.info(f"[ingest] skip {url}: {reason}")
            return None
    key = f"{study_root}/research/raw/{slug}.md"
    bytes_written = await storage.write(key, content, content_type = "text/markdown")
    return ManifestEntry(url = url, slug = slug, tier = tier, bytes = bytes_written)




# =============================================================================
# Public API — tier-aware dispatcher
# =============================================================================
async def ingest_framework_docs(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage,
    cache = None) -> IngestResult:
    """
    Route to the right ingestion strategy based on the resolver's tier +
    GitHub-discovery signals carried on `cfg`. Legacy callers that leave
    the hint fields None fall through to Tier 4 (Crawl4AI Playwright) so
    the existing /studies behavior is preserved.

    Strategy table (all implemented):
      github_discover == "readme_only"  → Tier-GH (GitHub tree + raw.md fetch)
      tier == 1                         → Tier 1  (one-shot llms-full.txt)
      tier == 2                         → Tier 2  (llms.txt + .md link fetch)
      tier == 3                         → Tier 3  (sitemap.xml + httpx + trafilatura)
      tier == 4 | None                  → Tier 4  (Crawl4AI + Playwright; this file)

    Each higher tier wraps its work in try/except and falls back to Tier 4
    on any raise (e.g. all llms.txt links off-subtree, repo has no docs/).
    The fallback means the pipeline always produces a corpus as long as
    Playwright can reach the docs host.
    """
    # Tier-GH short-circuit: github.com readme-only repos have no docs
    # host to Playwright-crawl; the GitHub API tree + raw.githubusercontent
    # fetch produces better results in ~5s instead of ~20 min. Falls back
    # to Tier 4 on any failure (e.g. repo with no docs/ + no README) so the
    # pipeline always produces a corpus.
    if cfg.github_discover == "readme_only":
        logger.info(
            f"[ingest] dispatcher → Tier-GH (readme_only) for {cfg.framework!r}"
        )
        try:
            from services.knowledge.github_ingest import ingest_github_tree
            return await ingest_github_tree(cfg, storage)
        except Exception as e:
            logger.warning(
                f"[ingest] Tier-GH failed for {cfg.framework!r} ({e}); "
                f"falling back to Tier 4 Playwright"
            )

    # Tier 1 — /llms-full.txt single-file fast path. Resolver's D-probe
    # already content-validated the file; Tier 1 just fetches it.
    if cfg.tier == 1:
        logger.info(f"[ingest] dispatcher → Tier 1 (llms-full.txt) for {cfg.framework!r}")
        from services.knowledge.llms_full_ingest import (
            ingest_llms_full_txt,
            TierOneManifestDetected,
        )
        try:
            return await ingest_llms_full_txt(cfg, storage)
        except TierOneManifestDetected as md:
            # OP-50 (2026-04-25, post-Run-12) — Docker case: llms-full.txt
            # was actually a llms.txt manifest. Falls through to Tier 2
            # (llms.txt parallel fetch) which knows how to consume the
            # URL: + Markdown: pointers natively. Skip Tier 4 Playwright
            # for this branch — Tier 2 is the right tool.
            logger.warning(
                f"[ingest] Tier 1 → Tier 2 downgrade for {cfg.framework!r}: {md}"
            )
            try:
                from services.knowledge.llms_txt_ingest import ingest_llms_txt
                return await ingest_llms_txt(cfg, storage)
            except Exception as e2:
                logger.warning(
                    f"[ingest] Tier 2 (after Tier 1 manifest downgrade) "
                    f"failed for {cfg.framework!r} ({e2}); "
                    f"falling back to Tier 4 Playwright"
                )
        except Exception as e:
            logger.warning(
                f"[ingest] Tier 1 failed for {cfg.framework!r} ({e}); "
                f"falling back to Tier 4 Playwright"
            )

    # Tier 3 — sitemap.xml parallel httpx fast path. Resolver D-probe already
    # content-validated the sitemap; Tier 3 expands it, filters to docs
    # subtree, parallel-fetches pages, and extracts markdown via trafilatura
    # (pure Python, F1 ≈ 0.791 on docs). ~2-5 min for 100-500 pages vs
    # ~20 min Tier 4 Playwright.
    if cfg.tier == 3:
        logger.info(f"[ingest] dispatcher → Tier 3 (sitemap httpx) for {cfg.framework!r}")
        try:
            from services.knowledge.sitemap_ingest import ingest_sitemap_httpx
            return await ingest_sitemap_httpx(cfg, storage)
        except Exception as e:
            logger.warning(
                f"[ingest] Tier 3 failed for {cfg.framework!r} ({e}); "
                f"falling back to Tier 4 Playwright"
            )

    # Tier 2 — /llms.txt index + parallel link fetch. Resolver D-probe
    # already content-validated the llms.txt; Tier 2 parses it via
    # AnswerDotAI's official llms_txt library, filters links to the docs
    # subtree, and parallel-fetches each (raw-save for *.md links,
    # trafilatura-extract for HTML links). ~30-90s for typical indexes.
    if cfg.tier == 2:
        logger.info(f"[ingest] dispatcher → Tier 2 (llms.txt) for {cfg.framework!r}")
        try:
            from services.knowledge.llms_txt_ingest import ingest_llms_txt
            return await ingest_llms_txt(cfg, storage)
        except Exception as e:
            logger.warning(
                f"[ingest] Tier 2 failed for {cfg.framework!r} ({e}); "
                f"falling back to Tier 4 Playwright"
            )

    # Default path: Tier 4 (Crawl4AI Playwright). Handles cfg.tier == 4
    # explicitly AND any None/stubbed case above. Runs the existing
    # 700-LoC Crawl4AI block as-is.
    return await _ingest_crawl4ai(cfg, storage, cache)


async def _ingest_crawl4ai(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage,
    cache = None) -> IngestResult:
    """
    Tier 4 — Crawl4AI v0.8 with Playwright rendering.

    PIPELINE (one HTTP round, no tier fallbacks):
      1. AsyncUrlSeeder discovers URLs from `sitemap.xml` + Common Crawl
         index. A sitemap soft-404 (Next.js returning 200+HTML) can't poison
         this path — the seeder validates URL shapes itself.
      2. Optionally pre-filter discovered URLs with our domain/language/URL
         blocklist to save bandwidth.
      3. AsyncWebCrawler.arun_many() fetches every URL via a real Chromium
         browser → JS SPAs (Next.js, React Router, Vue) are rendered; Cloudflare
         bot-check (403 for httpx) passes because requests look like a
         browser.
      4. Each successful result's fit_markdown goes through the post-fetch
         quality gate (`min_page_chars`, `max_link_text_ratio`) and is written
         to MinIO.
      5. If AsyncUrlSeeder returns zero URLs (rare — site has no sitemap, no
         CC index), we fall back to a BFS crawl from `docs_url` alone.

    Anti-bot resilience:
      - Playwright Chromium with a real User-Agent, cookies, JS support
      - Optional HTTP proxy via `BROWSER_PROXY_URL` env (set to warp-proxy
        if you need egress-IP rotation)

    Raises RuntimeError if no pages were successfully scraped.
    """
    from crawl4ai import (
        AsyncUrlSeeder, SeedingConfig,
        AsyncWebCrawler, BrowserConfig,
        CrawlerRunConfig, CacheMode,
        LXMLWebScrapingStrategy,
    )
    from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher, RateLimiter
    # PruningContentFilter: replaces our hand-rolled _passes_content_quality
    # heuristics with Crawl4AI's documented, tree-aware filter. threshold_type
    # "dynamic" adjusts per-node based on <article>/<p>/<nav> importance.
    # Passed to CrawlerRunConfig via markdown_generator (NOT content_filter —
    # the latter kwarg doesn't exist on CrawlerRunConfig; fit_markdown is
    # the pipeline step that consumes the content filter).
    # Docs: https://docs.crawl4ai.com/core/fit-markdown/
    try:
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        _PRUNING_AVAILABLE = True
    except ImportError:
        _PRUNING_AVAILABLE = False
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
    from crawl4ai.deep_crawling.filters import (
        FilterChain, DomainFilter, URLPatternFilter,
    )
    from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer

    parsed = urlparse(cfg.docs_url)
    host = parsed.netloc
    docs_path = parsed.path or "/"

    # ---------------------------------------------------------------
    # Step 1 — Discover URLs via AsyncUrlSeeder (sitemap + Common Crawl)
    # ---------------------------------------------------------------
    # Constrain discovery to the docs subtree when `docs_url` has a
    # meaningful path. For `https://docs.pydantic.dev/latest/` the pattern
    # becomes `*/latest/*`; for a bare `https://react.dev` it stays None
    # (full domain).
    seed_pattern: Optional[str] = None
    if docs_path and docs_path not in ("/", ""):
        cleaned = docs_path.rstrip("/")
        seed_pattern = f"*{cleaned}*"

    seeder_cfg = SeedingConfig(
        source = "sitemap+cc",
        pattern = seed_pattern,
        max_urls = cfg.max_pages,
        extract_head = False,   # skip <title>/<meta> fetch to save time
    )
    discovered_urls: list[str] = []
    try:
        async with AsyncUrlSeeder() as seeder:
            results = await seeder.urls(host, seeder_cfg)
        discovered_urls = [
            d.get("url") for d in results
            if d.get("url") and d.get("status") in ("valid", "found")
        ]
        logger.info(
            f"[ingest] AsyncUrlSeeder found {len(discovered_urls)} URLs "
            f"for {host} (pattern={seed_pattern!r})"
        )
    except Exception as e:
        logger.warning(f"[ingest] AsyncUrlSeeder failed: {e} — will BFS-crawl from root")

    # Always include the user-supplied docs_url as a seed
    if cfg.docs_url not in discovered_urls:
        discovered_urls.insert(0, cfg.docs_url)

    # ---------------------------------------------------------------
    # Step 2 — Apply our URL filters before fetching (saves bandwidth)
    # ---------------------------------------------------------------
    allow, deny = _build_language_filter(cfg.language)
    allow.extend(cfg.extra_allow_patterns)
    deny.extend(cfg.extra_deny_patterns)

    def _keep(u: str) -> bool:
        # Same-host only
        if urlparse(u).netloc != host:
            return False
        # URL blocklist
        if _matches_any(u, deny):
            return False
        # Non-target-language localization paths
        if NON_TARGET_LANGUAGE_PATH_RE.search(urlparse(u).path):
            return False
        return True

    filtered_urls = [u for u in discovered_urls if _keep(u)]
    dropped = len(discovered_urls) - len(filtered_urls)
    logger.info(
        f"[ingest] pre-filter kept {len(filtered_urls)}/{len(discovered_urls)} URLs "
        f"(dropped {dropped} via blocklist/lang/domain)"
    )

    if not filtered_urls:
        raise RuntimeError(
            f"No URLs survived pre-filter for {cfg.docs_url!r}. "
            "Check that docs_url is reachable and the blocklist isn't too aggressive."
        )

    # ---------------------------------------------------------------
    # Step 3 — Fetch everything via Crawl4AI (Playwright-rendered)
    # ---------------------------------------------------------------
    import os as _os
    proxy_url = _os.environ.get("BROWSER_PROXY_URL", "").strip() or None
    # Reuse the cluster's existing Playwright CDP service instead of
    # installing Chromium inside this image (saves ~300MB + respects
    # the centralized browser-pool pattern the YouTube pipeline already uses).
    # Env var PLAYWRIGHT_CDP_HEADLESS is set by the Helm configmap.
    cdp_endpoint = _os.environ.get("PLAYWRIGHT_CDP_HEADLESS", "").strip() or None
    cdp_ws_url = _resolve_cdp_ws_url(cdp_endpoint) if cdp_endpoint else None
    if cdp_ws_url:
        logger.info(f"[ingest] using remote Playwright CDP: {cdp_ws_url[:80]}…")
        browser_config = BrowserConfig(
            browser_type = "chromium",
            use_managed_browser = True,
            cdp_url = cdp_ws_url,
            headless = True,
            verbose = False,
        )
    else:
        logger.info("[ingest] CDP endpoint unresolved — falling back to local Chromium")
        browser_config = BrowserConfig(
            browser_type = "chromium",
            headless = True,
            verbose = False,
            proxy = proxy_url,
        )

    # Hardened SPA crawl config. Tuned from Crawl4AI v0.8 docs + GitHub issues
    # #1138, #1198, #1367, #1585 — the "Failed on navigating ACS-GOTO" error is
    # Crawl4AI wrapping `net::ERR_ABORTED` from Playwright's `page.goto()`.
    # On Next.js SPAs that error fires when `<Link>` prefetch races the
    # navigation promise.
    #
    # Performance tuning applied from 2026-04-21 research (Crawl4AI v0.8
    # best-practices + Playwright concurrency notes):
    #   - wait_until="domcontentloaded": the `load` event waits for every
    #     image/font/iframe (wastes ~30-50% on resource-heavy pages); our
    #     JS predicate below is what actually guarantees Next.js hydration,
    #     so `load` was adding wasted I/O. BrowserStack Playwright waitUntil
    #     guide confirms `domcontentloaded` + explicit predicate is the
    #     faster pattern for SPAs.
    #   - wait_for JS predicate: explicit check that #__next (Next.js root)
    #     or main/article is in DOM. Unchanged.
    #   - delay_before_return_html=0.2: dropped from 1.0. The JS predicate
    #     already guarantees the mount; the fixed 1s buffer was costing
    #     ~1s × 2,360 pages ≈ 39 min of wasted wall time.
    #   - page_timeout=60_000: was 90_000. Fail-fast on dead routes; the
    #     retry pass uses 90s, so transient hiccups still get a patient
    #     second attempt.
    #   - scraping_strategy=LXMLWebScrapingStrategy: 10-20× faster parsing
    #     vs the deprecated BeautifulSoup path (Crawl4AI migration docs).
    #     Default in v0.8 but pinned explicitly to be safe.
    #   - max_retries=2: Crawl4AI anti-bot retry layer (covers blocked/soft-404);
    #     does NOT catch ERR_ABORTED but it's free.
    # PruningContentFilter (2026-04-21): tree-aware noise removal.
    # threshold=0.45 is below the docs' default 0.48 to keep more content on
    # doc sites (fewer false-positive drops of "thin" but valid pages like
    # changelogs). threshold_type="dynamic" per-node scoring handles the
    # sidebar/nav chrome that our hand-rolled link-ratio heuristic approximated.
    # Build markdown generator with pruning filter (if available).
    # fit_markdown goes through DefaultMarkdownGenerator's content_filter
    # pipeline step — that's the documented route.
    if _PRUNING_AVAILABLE:
        _md_generator = DefaultMarkdownGenerator(
            content_filter = PruningContentFilter(
                threshold = 0.45, threshold_type = "dynamic", min_word_threshold = 5,
            ),
        )
    else:
        _md_generator = None
    crawler_cfg_base = CrawlerRunConfig(
        cache_mode = CacheMode.BYPASS,
        wait_until = "domcontentloaded",
        wait_for = "js:() => document.readyState === 'complete' && !!document.querySelector('#__next, main, article')",
        delay_before_return_html = 0.2,
        word_count_threshold = 50,
        excluded_tags = ["nav", "footer", "aside"],
        exclude_external_links = True,
        scraping_strategy = LXMLWebScrapingStrategy(),
        markdown_generator = _md_generator,
        stream = True,
        page_timeout = 60_000,
        max_retries = 2,
        verbose = False,
    )
    # Retry config: patient timeouts + longer post-load settle so the
    # slowest routes can hydrate. Runs URLs that ERR_ABORTED in primary.
    crawler_cfg_retry = CrawlerRunConfig(
        cache_mode = CacheMode.BYPASS,
        wait_until = "domcontentloaded",
        wait_for = "js:() => document.readyState === 'complete' && !!document.querySelector('#__next, main, article')",
        delay_before_return_html = 2.0,
        word_count_threshold = 50,
        excluded_tags = ["nav", "footer", "aside"],
        exclude_external_links = True,
        scraping_strategy = LXMLWebScrapingStrategy(),
        markdown_generator = _md_generator,
        stream = True,
        page_timeout = 90_000,
        max_retries = 2,
        verbose = False,
    )

    # Dispatchers cap concurrent Playwright pages on the SHARED remote CDP
    # browser. Without this, arun_many floods the shared context and triggers
    # the cross-task navigation races responsible for most ERR_ABORTEDs
    # (issue #1198).
    #
    # MemoryAdaptiveDispatcher (not Semaphore): the Semaphore variant lacks
    # `run_urls_stream` — stream=True + arun_many fails with AttributeError
    # (Crawl4AI issues #857, #703). Memory variant implements both batch
    # and stream dispatch paths and `max_session_permit` IS the real gate.
    #
    # Concurrency LOWERED from 15 → 4 (2026-04-21 research):
    #   - Issue #1326 (maintainer "Root caused") — "Target page closed"
    #     error reproduces reliably above ~4 parallel pages regardless of
    #     host resources. Earlier 15-slot runs hit 99.4% of failures from
    #     this error class on shared CDP.
    #   - Issue #1927 (OPEN, 21 Apr 2026) — BFS's internal dispatcher
    #     *ignores* max_session_permit anyway, so our 15 gave false sense
    #     of throttling.
    #   - Lower concurrency = fewer context races. 4 is the empirical
    #     sweet spot below the race threshold.
    dispatcher_primary = MemoryAdaptiveDispatcher(
        max_session_permit = 4,
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

    # If the seeder returned nothing, fall back to BFS deep-crawl from root
    use_bfs = len(filtered_urls) == 1 and filtered_urls[0] == cfg.docs_url

    # ---------------------------------------------------------------
    # Resume-from-cache (unified — works in BOTH arun_many AND BFS modes)
    # ---------------------------------------------------------------
    # A previous crashed run of the same (framework, version) may have
    # populated `_cache/ingestion/.../raw/` with some pages. The cache
    # persists a `.meta.json` sidecar for every saved page containing the
    # original URL + tier, so we can rebuild the full URL → slug map even
    # when we're about to enter BFS mode (where the initial URL list is
    # empty and partition-by-slug isn't possible).
    #
    # Two-level skip:
    #   1. PRE-LIST the stable study_root — after folder unification
    #      (same (user, fw, ver, level) → same folder), the study_root
    #      is likely already populated from a prior run. Anything already
    #      there is a ZERO-RTT skip — no restore copy, no head_object.
    #   2. For the remaining cached entries, server-side CopyObject
    #      (~30ms each vs ~1s read+write in the old path).
    already_cached_slugs: set[str] = set()
    already_cached_urls: set[str] = set()
    slugs_with_meta: set[str] = set()   # slugs that already have a .meta.json sidecar
    cached_entries: list[dict] = []
    already_in_study: set[str] = set()
    if cache is not None:
        try:
            cached_entries = await cache.get_cached_manifest(
                cfg.framework, cfg.version,
            )
            already_cached_slugs = {e["slug"] for e in cached_entries}
            already_cached_urls = {e["url"] for e in cached_entries if e.get("url")}
            slugs_with_meta = {e["slug"] for e in cached_entries if e.get("url")}
            if cached_entries:
                logger.info(
                    f"[ingest] cache has {len(cached_entries)} already-ingested "
                    f"pages for ({cfg.framework!r}, {cfg.version!r}) — resume-skip "
                    f"({len(already_cached_urls)} with known URL, "
                    f"{len(cached_entries) - len(already_cached_urls)} slug-only)"
                )
            # Pre-list study_root/research/raw/ to build "already materialized"
            # set. One paginated LIST vs 290-884 head_object calls.
            try:
                study_keys = await storage.list(f"{cfg.study_root}/research/raw/")
                for k in study_keys:
                    if k.endswith(".md"):
                        fname = k.rsplit("/", 1)[-1]
                        already_in_study.add(fname[: -len(".md")])
                if already_in_study:
                    logger.info(
                        f"[ingest] study_root already has {len(already_in_study)} "
                        f"pages materialized (unified folder resume) — these need "
                        f"no copy"
                    )
            except Exception as e:
                logger.warning(f"[ingest] study_root pre-list failed ({e}); will fall back to per-slug copy")
        except Exception as e:
            logger.warning(f"[ingest] cache probe failed ({e}); proceeding without resume")
            cached_entries = []

    # Partition seeded URLs (only meaningful outside BFS — in BFS mode
    # filtered_urls is just [docs_url], and URLs are discovered live).
    urls_to_crawl: list[str] = []
    urls_already_cached: list[tuple[str, str]] = []  # [(slug, url), ...]
    for u in filtered_urls:
        slug = _slugify(u)
        if slug in already_cached_slugs:
            urls_already_cached.append((slug, u))
        else:
            urls_to_crawl.append(u)

    manifest: list[ManifestEntry] = []
    skipped_urls: list[str] = []

    # Step A — Restore EVERY cached page into study_root (both modes).
    # In arun_many mode, this overlaps with `urls_already_cached` above
    # (dedupe via `restored_slugs`). In BFS mode, this is the primary way
    # we avoid re-fetching work — BFS can't partition upfront, so the full
    # cached set is restored here and then a URL filter blocks the BFS
    # walker from visiting them.
    #
    # Three-tier skip (fastest first):
    #   1. SKIP-IN-STUDY: slug already materialized at study_root (zero RTT,
    #      just a membership check against `already_in_study`). Only adds
    #      a ManifestEntry with known size=0; the actual bytes are there.
    #   2. SERVER-COPY: slug in cache but not in study_root — use MinIO
    #      CopyObject (1 RTT, no body transfer). Via
    #      `cache.copy_ingested_page_to_study`.
    #   3. BATCHED (chunks of 20): limits concurrent client contexts to
    #      avoid aiobotocore pool saturation — the IncompleteBody deadlock
    #      issue we hit at 290 concurrent writes.
    restored_slugs: set[str] = set()
    if cached_entries and cache is not None:
        _RESTORE_BATCH = 20
        # Tier 1 — zero-cost skip for slugs already in study_root
        zero_cost_slugs: list[dict] = []
        needs_copy: list[dict] = []
        for e in cached_entries:
            if e["slug"] in already_in_study:
                zero_cost_slugs.append(e)
            else:
                needs_copy.append(e)
        if zero_cost_slugs:
            for e in zero_cost_slugs:
                manifest.append(ManifestEntry(
                    url = e.get("url") or f"cache://{e['slug']}",
                    slug = e["slug"],
                    tier = e.get("tier") or "crawl4ai",
                    bytes = 0,  # unknown without a head, but content is present
                ))
                restored_slugs.add(e["slug"])
            logger.info(
                f"[ingest] skipped {len(zero_cost_slugs)} cached pages already in "
                f"study_root (zero RTT — unified folder resume)"
            )
        # Tier 2 — server-side CopyObject for the rest
        async def _restore(entry: dict) -> Optional[ManifestEntry]:
            slug = entry["slug"]
            url = entry.get("url") or f"cache://{slug}"
            try:
                bytes_written = await cache.copy_ingested_page_to_study(
                    cfg.framework, cfg.version, slug, cfg.study_root,
                )
                return ManifestEntry(
                    url = url, slug = slug,
                    tier = entry.get("tier") or "crawl4ai",
                    bytes = bytes_written,
                )
            except Exception as e:
                logger.warning(f"[ingest] cache restore failed for {slug}: {e}")
                return None
        for i in range(0, len(needs_copy), _RESTORE_BATCH):
            batch = needs_copy[i : i + _RESTORE_BATCH]
            batch_results = await asyncio.gather(
                *(_restore(e) for e in batch),
                return_exceptions = True,
            )
            for entry in batch_results:
                if isinstance(entry, ManifestEntry):
                    manifest.append(entry)
                    restored_slugs.add(entry.slug)
                elif isinstance(entry, Exception):
                    logger.warning(f"[ingest] cache restore raised: {entry}")
            if (i // _RESTORE_BATCH) % 5 == 0 and needs_copy:
                logger.info(
                    f"[ingest] restore progress (server-copy): "
                    f"{len(restored_slugs) - len(zero_cost_slugs)}/"
                    f"{len(needs_copy)} pages copied"
                )
        logger.info(
            f"[ingest] restored {len(restored_slugs)}/{len(cached_entries)} "
            f"cached pages → {cfg.study_root} "
            f"({len(zero_cost_slugs)} zero-cost, {len(needs_copy)} server-copy)"
        )

    # Step B — Stream-crawl the remaining URLs
    async with AsyncWebCrawler(config = browser_config) as crawler:
        # Block Next.js prefetch (_next/data/*) + heavy assets via hook.
        # Rationale (2026-04-21 research):
        #   - Next.js <Link> components prefetch `/_next/data/*.json` eagerly
        #     and race page.goto() — direct cause of the ~70 ACS-GOTO
        #     net::ERR_ABORTED failures we saw on reference.langchain.com.
        #   - Images/fonts/CSS are irrelevant for docs extraction but dominate
        #     the `readyState=complete` wait.
        # Hook: on_page_context_created fires right after Playwright creates
        # the context, so we intercept before any navigation occurs.
        async def _block_heavy_resources(page, context, **kw):
            try:
                await context.route("**/_next/data/**", lambda r: r.abort())
                await context.route(
                    "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,otf,mp4,webm}",
                    lambda r: r.abort(),
                )
                await context.route("**/*.css", lambda r: r.abort())
            except Exception as _hook_e:  # noqa — don't let hook errors kill the crawl
                logger.warning(f"[ingest] route-hook failed (non-fatal): {_hook_e}")
        try:
            crawler.crawler_strategy.set_hook(
                "on_page_context_created", _block_heavy_resources,
            )
        except Exception as _e:
            logger.warning(f"[ingest] could not install route hook (non-fatal): {_e}")
        if use_bfs:
            logger.info(
                f"[ingest] seeder empty — BFS-crawling from {cfg.docs_url} "
                f"(cached-skip set has {len(already_cached_urls)} URLs)"
            )
            # Build filter chain:
            #   1. DomainFilter — same host only.
            #   2. URLPatternFilter(scope_pattern) — when docs_url has a
            #      meaningful path (e.g. "/python/deepagents"), restrict BFS
            #      to that subtree. Without this, BFS wanders into siblings
            #      like /python/langchain/ and /python/langsmith/ (observed
            #      in 2026-04-21 run that crawled 800+ pages beyond the
            #      DeepAgents scope). Same pattern shape AsyncUrlSeeder
            #      uses (`*{cleaned}*`) so scope matches whether we got
            #      there via seeder or BFS.
            #   3. URLPatternFilter(cached_urls, reverse=True) — cut cached
            #      URLs so BFS never re-fetches them.
            filter_chain_items: list = [DomainFilter(allowed_domains = [host])]
            if seed_pattern:
                filter_chain_items.append(
                    URLPatternFilter(patterns = [seed_pattern])
                )
                logger.info(f"[ingest] BFS scope filter: {seed_pattern!r}")
            if already_cached_urls:
                filter_chain_items.append(
                    URLPatternFilter(
                        patterns = list(already_cached_urls),
                        reverse = True,  # reverse=True → REJECT matching URLs
                    )
                )
            # Use Crawl4AI's clone() to avoid leaking private attrs into __init__.
            bfs_strategy = BFSDeepCrawlStrategy(
                max_depth = cfg.max_depth,
                include_external = False,
                max_pages = cfg.max_pages,
                filter_chain = FilterChain(filter_chain_items),
                url_scorer = KeywordRelevanceScorer(
                    keywords = DEFAULT_SCORER_KEYWORDS,
                    weight = 0.7,
                ),
            )
            # CrawlerRunConfig exposes `.clone(**overrides)` for safe copy-with-change
            if hasattr(crawler_cfg_base, "clone"):
                bfs_cfg = crawler_cfg_base.clone(deep_crawl_strategy = bfs_strategy)
            else:
                # Fallback: construct fresh from known kwargs (no private attrs)
                bfs_cfg = CrawlerRunConfig(
                    cache_mode = CacheMode.BYPASS,
                    wait_for = "css:body",
                    word_count_threshold = 50,
                    excluded_tags = ["nav", "footer", "aside"],
                    exclude_external_links = True,
                    stream = False,
                    page_timeout = 30_000,
                    verbose = False,
                    deep_crawl_strategy = bfs_strategy,
                )
            # BFS is only invoked when the seeder returned nothing, i.e. the
            # queue is just {docs_url}. Stream-iterate results as pages are
            # rendered + link-extracted — each write lands in MinIO live.
            stream_iter = await crawler.arun(cfg.docs_url, config = bfs_cfg)
        else:
            logger.info(
                f"[ingest] stream-crawling {len(urls_to_crawl)} URLs via Crawl4AI "
                f"(proxy={'on' if proxy_url else 'off'}, "
                f"already_cached={len(urls_already_cached)})"
            )
            if not urls_to_crawl:
                stream_iter = None
            else:
                # Per-URL session_id → isolated BrowserContext per URL
                # (2026-04-21 research, Crawl4AI issue #1379). Shared contexts
                # cause ~99% of "Target page, context or browser has been
                # closed" failures (787 of 792 in the prior run). Assigning
                # a unique session_id per URL forces Crawl4AI's BrowserManager
                # to produce a fresh (Page, Context) triple — one dying page
                # can't poison siblings.
                import uuid as _uuid
                if hasattr(crawler_cfg_base, "clone"):
                    per_url_configs = [
                        crawler_cfg_base.clone(
                            session_id = f"crawl-{_uuid.uuid4().hex[:12]}",
                        )
                        for _ in urls_to_crawl
                    ]
                    stream_iter = await crawler.arun_many(
                        urls_to_crawl,
                        config = per_url_configs,
                        dispatcher = dispatcher_primary,
                    )
                else:
                    # Fallback: single config if clone() missing (older Crawl4AI)
                    stream_iter = await crawler.arun_many(
                        urls_to_crawl,
                        config = crawler_cfg_base,
                        dispatcher = dispatcher_primary,
                    )

        # ---------------------------------------------------------------
        # Step C — Stream-consumer helper. Returns the list of URLs that
        # failed due to NAVIGATION errors (transient — retry candidates);
        # quality-gate failures etc. go to `skipped_urls` with no retry.
        # ---------------------------------------------------------------
        async def _consume_stream(stream, label: str) -> list[str]:
            nav_failed: list[str] = []
            async for r in stream:
                if not getattr(r, "success", False):
                    url = getattr(r, "url", "?")
                    err = str(getattr(r, "error_message", "no detail"))
                    logger.info(f"[ingest][{label}] crawl failed {url}: {err[:120]}")
                    # Only retry navigation/timeout-class errors. Explicit
                    # 4xx/5xx or content-level refusals shouldn't be retried.
                    transient = (
                        "ACS-GOTO" in err
                        or "Timeout" in err
                        or "Navigation" in err
                        or "timeout" in err
                        or "net::ERR" in err
                    )
                    if transient and label == "primary":
                        nav_failed.append(url)
                    else:
                        skipped_urls.append(url)
                    continue
                url = r.url
                # Apply deny-list on the live-discovered URL (BFS may have
                # discovered pages our seeder-pass didn't filter).
                if _matches_any(url, deny) or NON_TARGET_LANGUAGE_PATH_RE.search(urlparse(url).path):
                    skipped_urls.append(url)
                    continue
                # Already-cached short-circuit: if we restored this slug
                # upfront, don't re-write it (the BFS URL filter should
                # have blocked this URL, but older cache entries without
                # .meta.json slip through — defensive check).
                #
                # BACKFILL: if this is a slug-only entry (no sidecar),
                # write a .meta.json now with the live URL. After one run,
                # every cached page has URL metadata, the BFS URL filter
                # blocks the page on future runs, and we never pay the
                # re-fetch tax again. Idempotent — adds a slug to the
                # in-memory set so we don't write the sidecar twice
                # within the same run.
                _early_slug = _slugify(url)
                if _early_slug in restored_slugs:
                    if cache is not None and _early_slug not in slugs_with_meta:
                        try:
                            await cache.save_sidecar_only(
                                cfg.framework, cfg.version, _early_slug,
                                url, "crawl4ai",
                            )
                            slugs_with_meta.add(_early_slug)
                        except Exception as e:
                            logger.warning(
                                f"[ingest] sidecar backfill failed for "
                                f"{_early_slug}: {e}"
                            )
                    continue
                md = None
                if hasattr(r, "markdown"):
                    md = getattr(r.markdown, "fit_markdown", None) or getattr(r.markdown, "raw_markdown", None)
                if not md:
                    skipped_urls.append(url)
                    continue
                # Quality gate (min chars, link-text ratio)
                keep, reason = _passes_content_quality(
                    md,
                    min_chars = cfg.min_page_chars,
                    max_link_text_ratio = cfg.max_link_text_ratio,
                )
                if not keep:
                    logger.info(f"[ingest][{label}] skip {url}: {reason}")
                    skipped_urls.append(url)
                    continue
                slug = _slugify(url)
                try:
                    if cache is not None:
                        bytes_written = await cache.save_ingested_page(
                            cfg.framework, cfg.version, cfg.study_root,
                            slug, md, url, "crawl4ai",
                        )
                    else:
                        bytes_written = await storage.write(
                            f"{cfg.study_root}/research/raw/{slug}.md",
                            md, content_type = "text/markdown",
                        )
                except Exception as e:
                    logger.warning(f"[ingest][{label}] write failed for {url}: {e}")
                    skipped_urls.append(url)
                    continue
                manifest.append(ManifestEntry(
                    url = url, slug = slug, tier = "crawl4ai", bytes = bytes_written,
                ))
                # Log every successful write for full crawl visibility.
                logger.info(
                    f"[ingest][{label}] progress: {len(manifest)} pages written "
                    f"(latest slug={slug})"
                )
            return nav_failed

        # Primary pass — wide net, fast config
        nav_failed_primary: list[str] = []
        if stream_iter is not None:
            nav_failed_primary = await _consume_stream(stream_iter, label = "primary")

        # ---------------------------------------------------------------
        # Step D — Retry pass for URLs that failed due to transient
        # navigation errors (ACS-GOTO races on Next.js SPAs etc.). Uses
        # a more patient config: 120s page_timeout, 2s settle delay, and
        # serializes requests through semaphore_count=1 so there are no
        # cross-task context races.
        #
        # Runs in BOTH modes: BFS's deep-crawl strategy handles link
        # discovery retries but not navigation-failure retries on the
        # URLs it already attempted — so arun_many over the collected
        # `nav_failed_primary` list is the right tool either way.
        # ---------------------------------------------------------------
        if nav_failed_primary:
            logger.info(
                f"[ingest] retry pass: {len(nav_failed_primary)} URLs "
                f"failed transiently; retrying with hardened config "
                f"(page_timeout=120s, delay=2s, semaphore=1)"
            )
            # Per-URL session_id for retry pass too (same rationale)
            import uuid as _uuid
            if hasattr(crawler_cfg_retry, "clone"):
                per_url_retry_configs = [
                    crawler_cfg_retry.clone(
                        session_id = f"retry-{_uuid.uuid4().hex[:12]}",
                    )
                    for _ in nav_failed_primary
                ]
                retry_stream = await crawler.arun_many(
                    nav_failed_primary,
                    config = per_url_retry_configs,
                    dispatcher = dispatcher_retry,
                )
            else:
                retry_stream = await crawler.arun_many(
                    nav_failed_primary,
                    config = crawler_cfg_retry,
                    dispatcher = dispatcher_retry,
                )
            still_failed = await _consume_stream(retry_stream, label = "retry")
            if still_failed:
                # URLs that failed BOTH passes — log and treat as skipped
                # (they'll appear in DEBT.md via the critic/assembler).
                logger.warning(
                    f"[ingest] {len(still_failed)} URLs failed after retry; "
                    "adding to skipped_urls"
                )
                skipped_urls.extend(still_failed)

    if not manifest:
        raise RuntimeError(
            f"Crawl produced no usable pages for {cfg.docs_url!r}. "
            f"Attempted {len(filtered_urls)} URLs; all failed the quality gate or "
            "returned empty markdown. Check docs_url validity + network egress."
        )

    total = sum(e.bytes for e in manifest)
    logger.info(
        f"[ingest] OK — {len(manifest)} files, {total} bytes "
        f"({len(skipped_urls)} skipped)"
    )
    return IngestResult(
        tier_used = "crawl4ai",
        total_files = len(manifest),
        total_bytes = total,
        manifest = manifest,
        skipped_urls = skipped_urls,
    )
