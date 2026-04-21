"""
Knowledge Distiller — Docs Probe (Stage D of the resolver)

Content-validated probe of `/llms-full.txt`, `/llms.txt`, `/sitemap.xml`
under a docs_url. Classifies the site into Tier 1-4 for the crawler:

  Tier 1 — /llms-full.txt is VALID → one file, done in seconds
  Tier 2 — /llms.txt     is VALID → index + parallel .md fetch, ~1 min
  Tier 3 — /sitemap.xml  is VALID → enumerate URLs, filter, fetch
  Tier 4 — all three MISSING/FAKE → full Playwright crawl, ~20 min

Why CONTENT validation and not just HTTP 200?
  SPA docs sites (Next.js, Docusaurus, etc.) return 200 + HTML SPA shell for
  arbitrary paths. Reference case: reference.langchain.com/python/deepagents/
  llms-full.txt returned 200 + HTML that was NOT a real llms-full.txt file.
  This module inspects the body, not just the status.

Reuses the validators from scripts/probe_llmstxt_coverage.py verbatim — same
thresholds, same markers. That script is the authority on what's "VALID";
this module just packages it for the resolver runtime.
"""
import asyncio
import logging
import os
import random
import re
import xml.etree.ElementTree as ET
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx

from schemas.knowledge.resolver import (
    ProbeResult,
    RootLivenessProbe,
    RootLivenessStatus,
    SpotCheckItem,
    SpotCheckResult,
    SpotCheckStatus,
    Tier,
    TierEvidence,
    TierProbe,
)


# Multi-tenant git hosts — host-root probing is meaningless here because
# `<host>/llms.txt` and `<host>/sitemap.xml` belong to the platform itself,
# not to any specific repo. Probing them produces false-positive Tier 2/3
# classifications for README-only projects (see: py-spy regression).
_MULTI_TENANT_GIT_HOSTS = {
    "github.com",
    "www.github.com",
    "gitlab.com",
    "www.gitlab.com",
    "bitbucket.org",
    "www.bitbucket.org",
    "codeberg.org",
    "www.codeberg.org",
}


# Probe-result ranking: prefer VALID, then SPA_FAKE (a real response, wrong
# shape), then MISSING (clean 404), last ERROR. Used to merge per-file probes
# across the docs_url-as-given AND the host root.
_RESULT_RANK: dict[ProbeResult, int] = {
    "VALID": 3,
    "SPA_FAKE": 2,
    "MISSING": 1,
    "ERROR": 0,
}


# =============================================================================
# Stage D0 — root-URL liveness
# =============================================================================
# Docs-site signals — if we see ≥2 of these in the body, the page is a real
# documentation site rather than a parked domain or dead SPA shell.
_DOCS_SIGNAL_PATTERNS: list[tuple[str, str]] = [
    ("nav",             r"<nav\b"),
    ("sidebar",         r"(?:class|id)\s*=\s*\"[^\"]*(?:sidebar|toc|navigation)"),
    ("headings",        r"<h[1-3]\b"),
    ("code",            r"<(?:code|pre)\b"),
    ("docs_word",       r"(?i)\b(docs?|documentation|api reference|guide|tutorial)\b"),
    ("markdown_word",   r"(?i)\bmarkdown\b|\.md\b"),
    ("search_ui",       r"(?:class|id)\s*=\s*\"[^\"]*(?:search|algolia)"),
]

# Parked-domain markers — if the body contains any of these near the top, it's
# almost certainly a domain-for-sale page rather than real docs.
_PARKED_MARKERS = [
    "this domain is for sale",
    "domain is for sale",
    "buy this domain",
    "purchase this domain",
    "sedo.com/search",
    "hugedomains.com",
    "domainmarket.com",
    "parking service",
    "godaddy.com/domainsearch",
    "namecheap.com/market",
]

# Minimum text-content size after tag stripping. Below this a page is an
# "empty shell" — SSR didn't render anything meaningful; likely a dead route.
_MIN_LIVE_TEXT_CHARS = 400

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html: str) -> str:
    """Cheap tag strip — good enough for 'is there meaningful text' checks."""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _detect_signals(body: str) -> list[str]:
    """Return the list of docs-signal names detected in the body."""
    hits: list[str] = []
    head = body[:50_000]  # only scan the first 50KB
    for name, pattern in _DOCS_SIGNAL_PATTERNS:
        if re.search(pattern, head):
            hits.append(name)
    return hits


def _is_parked(body: str) -> bool:
    lower = body[:20_000].lower()
    return any(marker in lower for marker in _PARKED_MARKERS)


async def _probe_root_liveness(
    client: httpx.AsyncClient,
    docs_url: str) -> RootLivenessProbe:
    """
    Stage D0. Fetch docs_url, inspect the body, classify into
    LIVE / EMPTY_SHELL / PARKED / DEAD / ERROR.

    LIVE       — ≥2 docs signals AND ≥400 chars of text-content AND not parked
    EMPTY_SHELL — reachable 2xx/3xx but body text is < 400 chars (SPA skeleton)
    PARKED     — any of _PARKED_MARKERS near top of body
    DEAD       — HTTP ≥400, or final URL host doesn't match request host
    ERROR      — network-level failure
    """
    original_host = (urlparse(docs_url).netloc or "").lower()
    try:
        resp = await client.get(docs_url)
    except httpx.TimeoutException:
        return RootLivenessProbe(
            url = docs_url, status = "ERROR", http_status = -1,
            reason = "timeout", bytes_read = 0,
        )
    except httpx.ConnectError as e:
        return RootLivenessProbe(
            url = docs_url, status = "ERROR", http_status = -2,
            reason = f"connect_error: {e}"[:200], bytes_read = 0,
        )
    except Exception as e:
        return RootLivenessProbe(
            url = docs_url, status = "ERROR", http_status = -3,
            reason = f"{type(e).__name__}: {e}"[:200], bytes_read = 0,
        )

    body = resp.text[:_MAX_BODY_BYTES]
    final_url = str(resp.url)
    final_host = (urlparse(final_url).netloc or "").lower()

    if resp.status_code >= 400:
        return RootLivenessProbe(
            url = docs_url, status = "DEAD", http_status = resp.status_code,
            reason = f"HTTP {resp.status_code}", bytes_read = len(body),
            final_url = final_url,
        )

    # Redirected off-host — sometimes a dying project redirects to a vendor
    # parking page or a generic marketing site. Flag that as DEAD so the
    # resolver can fall back rather than crawl the wrong org.
    if final_host and final_host != original_host and not final_host.endswith(
        "." + original_host
    ) and not original_host.endswith("." + final_host):
        return RootLivenessProbe(
            url = docs_url, status = "DEAD", http_status = resp.status_code,
            reason = f"redirected off-host to {final_host}",
            bytes_read = len(body),
            final_url = final_url,
        )

    if _is_parked(body):
        return RootLivenessProbe(
            url = docs_url, status = "PARKED", http_status = resp.status_code,
            reason = "domain-for-sale markers detected", bytes_read = len(body),
            final_url = final_url,
        )

    text = _strip_tags(body)
    signals = _detect_signals(body)

    if len(text) < _MIN_LIVE_TEXT_CHARS:
        return RootLivenessProbe(
            url = docs_url, status = "EMPTY_SHELL", http_status = resp.status_code,
            reason = f"only {len(text)} chars of text after tag-strip (SPA shell?)",
            bytes_read = len(body),
            docs_signals = signals,
            final_url = final_url,
        )

    if len(signals) < 2:
        # Real page, but doesn't look like docs — could be a marketing landing.
        # Treat as EMPTY_SHELL so the resolver surfaces the issue without
        # silently committing to a non-docs URL.
        return RootLivenessProbe(
            url = docs_url, status = "EMPTY_SHELL", http_status = resp.status_code,
            reason = f"only {len(signals)} docs signals — looks non-docs",
            bytes_read = len(body),
            docs_signals = signals,
            final_url = final_url,
        )

    return RootLivenessProbe(
        url = docs_url, status = "LIVE", http_status = resp.status_code,
        reason = f"{len(text)} chars, {len(signals)} signals",
        bytes_read = len(body),
        docs_signals = signals,
        final_url = final_url,
    )


# =============================================================================
# Stage D2 — tier-specific spot-check (sample URLs from the winning index)
# =============================================================================
_SPOT_SAMPLE_SIZE = 3
_SPOT_VALID_MIN_CHARS = 300         # chars of tag-stripped text considered "real"
_SPOT_HTML_MIN_BYTES = 1_500        # raw HTML shorter than this is a stub
_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_LLMS_MD_LINK_RE = re.compile(r"\bhttps?://\S+?\.md\b|\]\((\S+?\.md)\)|\b(\S+?\.md)\b")


def _extract_sitemap_urls(body: str, base_url: str) -> list[str]:
    """Parse <loc> entries; tolerate malformed XML via regex fallback."""
    urls: list[str] = []
    try:
        root = ET.fromstring(body)
        for elem in root.iter():
            if elem.tag.lower().endswith("}loc") or elem.tag.lower() == "loc":
                if elem.text and elem.text.strip():
                    urls.append(elem.text.strip())
    except ET.ParseError:
        urls = _SITEMAP_LOC_RE.findall(body)

    # Dedupe while preserving order
    seen = set()
    out: list[str] = []
    for u in urls:
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _extract_llms_md_links(body: str, base_url: str) -> list[str]:
    """
    llms.txt typically contains lines like:
        - [Guide](/docs/guide.md)
        - /docs/tutorial.md
        - https://docs.example.com/intro.md

    Extract all .md URLs; resolve relatives against base_url.
    """
    raw: list[str] = []
    for m in re.finditer(r"(https?://\S+?\.md)\b", body):
        raw.append(m.group(1))
    # Markdown link targets  [text](target.md)
    for m in re.finditer(r"\]\((\S+?\.md)\)", body):
        raw.append(m.group(1))

    resolved: list[str] = []
    seen = set()
    for r in raw:
        full = r if r.startswith(("http://", "https://")) else urljoin(base_url, r)
        if full in seen:
            continue
        seen.add(full)
        resolved.append(full)
    return resolved


async def _fetch_sample(
    client: httpx.AsyncClient,
    url: str) -> SpotCheckItem:
    try:
        resp = await client.get(url)
    except httpx.TimeoutException:
        return SpotCheckItem(url = url, status = "ERROR", http_status = -1, reason = "timeout")
    except httpx.ConnectError as e:
        return SpotCheckItem(url = url, status = "ERROR", http_status = -2, reason = f"connect_error: {e}"[:200])
    except Exception as e:
        return SpotCheckItem(url = url, status = "ERROR", http_status = -3, reason = f"{type(e).__name__}: {e}"[:200])

    body = resp.text[:_MAX_BODY_BYTES]

    if resp.status_code == 404:
        return SpotCheckItem(url = url, status = "MISSING", http_status = 404, reason = "404", bytes_read = len(body))
    if resp.status_code >= 400:
        return SpotCheckItem(
            url = url, status = "MISSING", http_status = resp.status_code,
            reason = f"HTTP {resp.status_code}", bytes_read = len(body),
        )

    # Sample URLs can legitimately be HTML pages (sitemap points at HTML),
    # markdown files (llms.txt links), or XML (sitemap subindexes). Decide
    # based on content.
    body_lower_head = body[:200].lstrip().lower()
    is_markdown = (
        url.endswith(".md") or
        (body.startswith("#") and "\n" in body[:500])
    )
    if is_markdown:
        if len(body) < 200:
            return SpotCheckItem(
                url = url, status = "EMPTY", http_status = resp.status_code,
                reason = f"markdown too short ({len(body)} bytes)",
                bytes_read = len(body),
            )
        return SpotCheckItem(
            url = url, status = "VALID", http_status = resp.status_code,
            reason = f"{len(body)} bytes markdown",
            bytes_read = len(body),
        )

    # HTML path — check for SPA shell + meaningful text
    if any(m in body_lower_head for m in ("<html", "<!doctype")):
        if len(body) < _SPOT_HTML_MIN_BYTES:
            return SpotCheckItem(
                url = url, status = "EMPTY", http_status = resp.status_code,
                reason = f"HTML shell too short ({len(body)} bytes)",
                bytes_read = len(body),
            )
        text = _strip_tags(body)
        if len(text) < _SPOT_VALID_MIN_CHARS:
            return SpotCheckItem(
                url = url, status = "EMPTY", http_status = resp.status_code,
                reason = f"only {len(text)} chars of text (SPA shell?)",
                bytes_read = len(body),
            )
        return SpotCheckItem(
            url = url, status = "VALID", http_status = resp.status_code,
            reason = f"{len(text)} chars of text",
            bytes_read = len(body),
        )

    # Plaintext / XML / other — accept if not trivially short
    if len(body) < 200:
        return SpotCheckItem(
            url = url, status = "EMPTY", http_status = resp.status_code,
            reason = f"body too short ({len(body)} bytes)",
            bytes_read = len(body),
        )
    return SpotCheckItem(
        url = url, status = "VALID", http_status = resp.status_code,
        reason = f"{len(body)} bytes",
        bytes_read = len(body),
    )


async def _spot_check(
    client: httpx.AsyncClient,
    source: Literal["sitemap", "llms_txt"],
    index_body: str,
    base_url: str) -> SpotCheckResult:
    """
    Draw `_SPOT_SAMPLE_SIZE` URLs from the index body and fetch each to
    confirm the index isn't pointing at stale / 404 / empty content.
    """
    if source == "sitemap":
        urls = _extract_sitemap_urls(index_body, base_url)
    else:
        urls = _extract_llms_md_links(index_body, base_url)

    if not urls:
        return SpotCheckResult(
            source = source, samples = [], valid_count = 0,
            total_count = 0, downgrade_applied = False,
        )

    sample_count = min(_SPOT_SAMPLE_SIZE, len(urls))
    # Prefer stratified sampling: head + middle + tail. Catches publishers
    # that keep legacy content at the top of the index but rot later pages.
    picks: list[str] = []
    if sample_count == 1:
        picks = [urls[0]]
    elif sample_count == 2:
        picks = [urls[0], urls[-1]]
    else:
        picks = [urls[0], urls[len(urls) // 2], urls[-1]]

    # If the index is large, replace the middle pick with a random sample for
    # extra robustness — catches rot concentrated in any specific band.
    if len(urls) >= 10 and sample_count == 3:
        picks[1] = random.choice(urls[1:-1])

    samples = await asyncio.gather(*(_fetch_sample(client, u) for u in picks))
    valid_count = sum(1 for s in samples if s.status == "VALID")
    total_count = len(samples)
    # Majority-rule downgrade — if <50% of samples came back valid, the index
    # is stale and the tier should drop by one so the crawler falls through
    # to the next strategy.
    downgrade = (valid_count * 2 < total_count) if total_count > 0 else False
    return SpotCheckResult(
        source = source, samples = list(samples),
        valid_count = valid_count, total_count = total_count,
        downgrade_applied = downgrade,
    )


logger = logging.getLogger(__name__)


# =============================================================================
# Validation rules — copied from scripts/probe_llmstxt_coverage.py
# =============================================================================
_HTML_MARKERS = ("<html", "<!doctype", "<!DOCTYPE", "<HTML")
_LLMS_FULL_MIN_SIZE = 500    # bytes — smaller = likely stub or error
_MARKDOWN_HEADING = "# "
_TIMEOUT_SECONDS = 10.0
_MAX_BODY_BYTES = 100_000    # cap the body read — 100KB is enough to validate shape


def _validate_llms_full(status: int, body: str) -> tuple[ProbeResult, str]:
    if status == 404:
        return "MISSING", "404"
    if status >= 400 or status != 200:
        return "MISSING", f"HTTP {status}"
    if any(m in body[:500] for m in _HTML_MARKERS):
        return "SPA_FAKE", "body is HTML (SPA shell)"
    if len(body) < _LLMS_FULL_MIN_SIZE:
        return "SPA_FAKE", f"body too short ({len(body)} bytes)"
    if _MARKDOWN_HEADING not in body[:2000]:
        return "SPA_FAKE", "no markdown heading in first 2KB"
    return "VALID", f"{len(body)} bytes"


def _validate_llms_txt(status: int, body: str) -> tuple[ProbeResult, str]:
    """llms.txt is usually smaller (index-style) — more lenient size check."""
    if status == 404:
        return "MISSING", "404"
    if status >= 400 or status != 200:
        return "MISSING", f"HTTP {status}"
    if any(m in body[:500] for m in _HTML_MARKERS):
        return "SPA_FAKE", "body is HTML (SPA shell)"
    if len(body) < 50:
        return "SPA_FAKE", f"body too short ({len(body)} bytes)"
    has_heading = "#" in body[:1000]
    has_md_url = ".md" in body[:4000]
    if not (has_heading or has_md_url):
        return "SPA_FAKE", "no markdown/md-url markers"
    return "VALID", f"{len(body)} bytes"


def _validate_sitemap(status: int, body: str) -> tuple[ProbeResult, str]:
    if status == 404:
        return "MISSING", "404"
    if status >= 400 or status != 200:
        return "MISSING", f"HTTP {status}"
    head = body[:500].lstrip()
    if not (head.startswith("<?xml") or head.startswith("<urlset") or head.startswith("<sitemapindex")):
        return "SPA_FAKE", "body not XML"
    if "<loc>" not in body[:4000]:
        return "SPA_FAKE", "no <loc> entries found"
    return "VALID", f"{body.count('<loc>')} <loc> entries"


# =============================================================================
# HTTP fetch
# =============================================================================
async def _fetch(
    client: httpx.AsyncClient,
    url: str) -> tuple[int, str]:
    """Fetch a URL, cap body at _MAX_BODY_BYTES, catch every I/O exception."""
    try:
        resp = await client.get(url)
        return resp.status_code, resp.text[:_MAX_BODY_BYTES]
    except httpx.TimeoutException:
        return -1, "timeout"
    except httpx.ConnectError as e:
        return -2, f"connect_error: {e}"
    except Exception as e:
        return -3, f"error: {type(e).__name__}: {e}"


async def _probe_one(
    client: httpx.AsyncClient,
    url: str,
    kind: Literal["llms_full", "llms_txt", "sitemap"]) -> tuple[TierProbe, str]:
    """Return (probe, body) — body kept for downstream spot-check sampling."""
    status, body = await _fetch(client, url)
    if status < 0:
        return (
            TierProbe(
                url = url, result = "ERROR",
                reason = body or "network error", bytes_read = 0,
            ),
            "",
        )
    if kind == "llms_full":
        result, reason = _validate_llms_full(status, body)
    elif kind == "llms_txt":
        result, reason = _validate_llms_txt(status, body)
    else:
        result, reason = _validate_sitemap(status, body)
    return (
        TierProbe(url = url, result = result, reason = reason, bytes_read = len(body)),
        body,
    )


# =============================================================================
# Public API — probe_and_classify
# =============================================================================
def _host_root(docs_url: str) -> str:
    """
    Return the scheme://host of `docs_url`. llms-full.txt / llms.txt /
    sitemap.xml are hosted at the DOMAIN root by convention — not at every
    subpath. Probing only the deep path misses valid files 99% of the time.
    """
    p = urlparse(docs_url)
    return f"{p.scheme}://{p.netloc}"


def _pick_merged(
    a: tuple[TierProbe, str],
    b: tuple[TierProbe, str] | None) -> tuple[TierProbe, str]:
    """
    Merge two (probe, body) pairs — prefer the higher-ranked probe, and keep
    THAT probe's body so the downstream spot-check samples from the winning
    index (not from the loser's body).
    """
    if b is None:
        return a
    pa, ba = a
    pb, bb = b
    if _RESULT_RANK[pa.result] >= _RESULT_RANK[pb.result]:
        return (pa, ba)
    return (pb, bb)


# =============================================================================
# GitHub docs-home discovery
# =============================================================================
# When the resolver lands on `github.com/{org}/{repo}`, the host-root probes
# pick up GitHub's PLATFORM-wide /llms.txt and /sitemap.xml — not docs for the
# repo. Fix (per 2026 community practice + llms-txt spec authors): query the
# GitHub repo API for the project's declared docs home (homepage field),
# GitHub Pages availability (has_pages), and default branch (for Tier-GH
# raw-markdown fetching downstream). Priority:
#   homepage > has_pages > README-only
# Docs:
#   - https://docs.github.com/en/rest/repos/repos#get-a-repository
#   - llms-txt spec: scoped to the owner-domain, not multi-tenant hosts
async def _github_repo_discover(
    client: httpx.AsyncClient,
    org: str,
    repo: str) -> dict | None:
    """
    Query `api.github.com/repos/{org}/{repo}`. Returns the relevant fields or
    None on any failure (rate-limited, 404, network error).

    Authenticated requests get 5000 req/hr instead of 60 — set
    `GITHUB_TOKEN` env var for production use; works unauthenticated for
    casual probing.
    """
    url = f"https://api.github.com/repos/{org}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "COELHONexus-KD-Resolver/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = await client.get(url, headers = headers)
    except Exception as e:
        logger.info(f"[github-discover] {org}/{repo} API error: {e}")
        return None
    if resp.status_code != 200:
        logger.info(
            f"[github-discover] {org}/{repo} HTTP {resp.status_code} "
            f"({'rate-limited' if resp.status_code == 403 else 'not found'})"
        )
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    homepage = (data.get("homepage") or "").strip() or None
    if homepage and not homepage.startswith(("http://", "https://")):
        # GitHub allows homepage to be bare hostnames like "example.com".
        homepage = f"https://{homepage}"
    return {
        "homepage": homepage,
        "has_pages": bool(data.get("has_pages")),
        "default_branch": data.get("default_branch") or "main",
        "archived": bool(data.get("archived")),
        "stargazers": int(data.get("stargazers_count") or 0),
    }


async def _upgrade_git_host_url(
    client: httpx.AsyncClient,
    docs_url: str) -> tuple[str, dict]:
    """
    If `docs_url` lives on a multi-tenant git host, try to discover the real
    documentation home. Returns (upgraded_url, discovery_metadata).

    Resolution priority (per 2026 docs-extraction conventions):
      1. homepage field — publisher's declared canonical docs site
      2. has_pages → f"{org}.github.io/{repo}" — GitHub Pages deployment
      3. README-only — keep the github URL; caller forces Tier 4

    `discovery_metadata` is surfaced in source_signals so the resolver
    response documents why docs_url changed (or didn't).
    """
    p = urlparse(docs_url)
    host = (p.netloc or "").lower()
    if host not in _MULTI_TENANT_GIT_HOSTS:
        return docs_url, {}
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        # Host-root or org-level URL — nothing to upgrade.
        return docs_url, {"github_discover": "no_repo_in_path"}
    org, repo = parts[0], parts[1]
    meta = await _github_repo_discover(client, org, repo)
    if meta is None:
        return docs_url, {"github_discover": "api_unavailable", "org": org, "repo": repo}

    if meta["homepage"]:
        logger.info(
            f"[github-discover] {org}/{repo} → homepage upgrade: {meta['homepage']}"
        )
        return meta["homepage"], {"github_discover": "homepage", **meta, "org": org, "repo": repo}

    if meta["has_pages"]:
        pages_url = f"https://{org}.github.io/{repo}"
        logger.info(
            f"[github-discover] {org}/{repo} → pages upgrade: {pages_url}"
        )
        return pages_url, {"github_discover": "pages", **meta, "org": org, "repo": repo}

    logger.info(
        f"[github-discover] {org}/{repo} → README-only (homepage='', has_pages=False)"
    )
    return docs_url, {"github_discover": "readme_only", **meta, "org": org, "repo": repo}


async def probe_and_classify(
    docs_url: str,
    timeout_s: float = _TIMEOUT_SECONDS) -> tuple[Tier, TierEvidence, dict]:
    """
    Full Stage D pipeline. Runs all probes in parallel, merges per file,
    classifies into Tier 1-4, then runs Stage D0 + D2 to catch cases the
    file probes miss:

      D0 (root liveness): fetch docs_url itself. Detects parked domains,
          dead SPA shells, off-host redirects. Without D0 a tier-1 URL can
          still point at a site that's effectively dead.

      D2 (spot-check): sample 2-3 URLs from the winning index (sitemap
          <loc> entries or llms.txt .md links). Catches stale indexes that
          reference 404 or empty-shell pages. Downgrade the tier when the
          majority fail — better to crawl with a rougher strategy than
          trust a VALID-but-stale index.

      GitHub upgrade: if docs_url lands on github.com/{org}/{repo}, query
          the GitHub API for the repo's declared homepage or GitHub Pages
          site, and re-probe there. Multi-tenant git hosts never get
          host-root probing (github.com/llms.txt is platform-wide, not
          framework-specific).

    Returns (tier, evidence, discovery_meta) where discovery_meta carries
    the GitHub-discovery verdict when applicable; `{}` otherwise. Never
    raises — network / validation failures surface as ERROR / MISSING
    probes that cascade down to Tier 4.
    """
    headers_api = {
        "User-Agent": "COELHONexus-KD-Resolver/1.0 (+https://rafaelcoelho1409.github.io)",
    }
    # GitHub discovery runs first — the resulting URL is what we actually probe.
    # Keep it on a separate client so we don't pollute the content-probe's
    # Accept header (API JSON vs docs HTML).
    async with httpx.AsyncClient(
        follow_redirects = True,
        timeout = httpx.Timeout(10.0, connect = 5.0),
        headers = headers_api,
    ) as api_client:
        upgraded_url, discovery_meta = await _upgrade_git_host_url(api_client, docs_url)

    # Short-circuit: README-only GitHub repo has no docs host to probe.
    # Force Tier 4 so the crawler pulls the README via its Tier-4 strategy
    # (raw.githubusercontent.com/{org}/{repo}/{default_branch}/README.md).
    if discovery_meta.get("github_discover") == "readme_only":
        placeholder = TierProbe(
            url = docs_url, result = "MISSING",
            reason = "github readme-only (no docs site, no GH Pages)",
            bytes_read = 0,
        )
        evidence = TierEvidence(
            llms_full_txt = placeholder,
            llms_txt = placeholder,
            sitemap_xml = placeholder,
            root_liveness = RootLivenessProbe(
                url = docs_url, status = "LIVE", http_status = 200,
                reason = "github repo page (README-only)",
                bytes_read = 0,
                docs_signals = ["github_readme"],
                final_url = docs_url,
            ),
            spot_check = None,
        )
        logger.info(
            f"[docs-probe] {docs_url} → tier=4 (github readme-only short-circuit)"
        )
        return 4, evidence, discovery_meta

    base = upgraded_url.rstrip("/")
    root = _host_root(upgraded_url)
    paths = [("llms_full", "/llms-full.txt"), ("llms_txt", "/llms.txt"), ("sitemap", "/sitemap.xml")]

    deep_urls = {kind: f"{base}{suffix}" for kind, suffix in paths}
    root_urls = {kind: f"{root}{suffix}" for kind, suffix in paths}
    # Never probe host-root on multi-tenant git hosts — github.com/llms.txt
    # is platform-wide, not framework-specific. After the upgrade step the
    # URL should have moved off github.com, but keep the guard for safety.
    root_host = (urlparse(root).netloc or "").lower()
    use_root = (
        base != root.rstrip("/")
        and root_host not in _MULTI_TENANT_GIT_HOSTS
    )

    headers = {
        "User-Agent": "COELHONexus-KD-Resolver/1.0 (+https://rafaelcoelho1409.github.io)",
        "Accept": "text/html, text/plain, text/markdown, application/xml, text/xml, */*",
    }
    timeout = httpx.Timeout(timeout = timeout_s, connect = 5.0)
    async with httpx.AsyncClient(
        follow_redirects = True,
        timeout = timeout,
        headers = headers,
        http2 = False,
    ) as client:
        # Fire ALL probes — D0 root liveness + 3 file probes (at deep AND
        # root paths) — in one asyncio.gather for minimum latency. D0
        # probes the UPGRADED url so github → homepage redirects land on
        # the real docs site.
        deep_tasks = [_probe_one(client, deep_urls[k], k) for k, _ in paths]
        root_tasks = [_probe_one(client, root_urls[k], k) for k, _ in paths] if use_root else []
        liveness_task = _probe_root_liveness(client, upgraded_url)

        gathered = await asyncio.gather(
            liveness_task,
            *deep_tasks,
            *root_tasks,
        )

        liveness: RootLivenessProbe = gathered[0]
        deep = gathered[1:4]
        root_probes: list[tuple[TierProbe, str] | None] = (
            list(gathered[4:7]) if use_root else [None, None, None]
        )

        # Merge best per file, keeping the winning body for D2
        merged = [_pick_merged(d, r) for d, r in zip(deep, root_probes)]
        full_probe, full_body = merged[0]
        txt_probe, txt_body = merged[1]
        map_probe, map_body = merged[2]

        # Tier waterfall — first VALID wins
        if full_probe.result == "VALID":
            tier: Tier = 1
        elif txt_probe.result == "VALID":
            tier = 2
        elif map_probe.result == "VALID":
            tier = 3
        else:
            tier = 4

        # Stage D2 — spot-check the winning index. Tier 1 (llms-full.txt)
        # is monolithic so no spot-check is needed — the validator already
        # confirmed size + markdown shape.
        spot: SpotCheckResult | None = None
        base_for_relative = root if use_root else base + "/"
        if tier == 2:
            spot = await _spot_check(client, "llms_txt", txt_body, base_for_relative)
        elif tier == 3:
            spot = await _spot_check(client, "sitemap", map_body, base_for_relative)

    # Apply majority-failure downgrade. A downgraded tier cascades:
    #   tier 2 → 3 → 4 as spot-check failures accumulate. The caller sees
    #   `downgrade_applied=True` on the evidence, so the audit trail is intact.
    if spot and spot.downgrade_applied:
        logger.info(
            f"[docs-probe] {docs_url}: spot-check downgrade "
            f"(valid {spot.valid_count}/{spot.total_count}) — tier {tier} → {tier+1}"
        )
        tier = min(4, tier + 1)

    # If D0 says the root is DEAD / PARKED, the whole site is unusable —
    # force Tier 4 so the caller can decide to null out docs_url entirely.
    # ERROR / EMPTY_SHELL are weaker signals; resolver penalizes confidence
    # but doesn't force a downgrade.
    if liveness.status in ("DEAD", "PARKED"):
        logger.info(
            f"[docs-probe] {docs_url}: root {liveness.status} — forcing tier 4"
        )
        tier = 4

    evidence = TierEvidence(
        llms_full_txt = full_probe,
        llms_txt = txt_probe,
        sitemap_xml = map_probe,
        root_liveness = liveness,
        spot_check = spot,
    )
    logger.info(
        f"[docs-probe] {docs_url} (→{upgraded_url}) → tier={tier} "
        f"(full={full_probe.result} txt={txt_probe.result} map={map_probe.result}) "
        f"root={liveness.status} "
        f"spot={spot.valid_count if spot else '-'}/{spot.total_count if spot else '-'} "
        f"{'[deep+root]' if use_root else '[root-only]'}"
    )
    return tier, evidence, discovery_meta
