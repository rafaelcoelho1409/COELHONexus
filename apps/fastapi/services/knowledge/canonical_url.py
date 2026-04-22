"""
Knowledge Distiller — Canonical URL Normalization (Stage B+)

Sits between Stage B (search candidates from Exa / Tavily / Jina) and Stage C
(LLM rerank) to collapse legacy / alias docs hosts onto the publisher-declared
canonical URL BEFORE the LLM sees the list. Two publisher signals drive the
substitution, in Google's own canonicalization priority order (stronger first):

  1. 301/302 redirects           — strongest; the publisher explicitly moved
                                   the URL. `follow_redirects=True` lands on
                                   the canonical.
  2. <link rel="canonical" href>  — meta signal the publisher sets on the
                                   served page. Common on Fern / Mintlify /
                                   Docusaurus docs sites.

After substitution we dedupe: if two hits collapse to the same canonical URL
we keep the first (providers order by relevance) and merge snippet data.

Motivating case (2026-04-22): search providers return both `docs.langchain.com/`
(new canonical) and `python.langchain.com/api_reference` (legacy v0.3) for a
LangChain query. `python.langchain.com/` 301s to docs.langchain.com, so
redirect-following alone fixes the common case. The residual /api_reference
sub-path that still serves standalone is handled by the LLM rerank's prompt
rules (see prompts.py — "prefer docs.{domain} over language subdomains").

Design notes:
- Pure function over list[SearchHit]; no side effects beyond HTTP probes.
- Bounded concurrency (Semaphore) to avoid blowing past provider rate limits.
- On any error (timeout, non-HTML, parse failure) we KEEP the original URL
  — normalization must never DROP hits. At worst it's a no-op.
- HEAD is not reliable (many Fern/Mintlify hosts return generic 200 HEAD with
  no Location) — a GET with a small byte cap is necessary to read the
  canonical meta tag.
"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse, urljoin

import httpx

from schemas.knowledge.resolver import SearchHit


logger = logging.getLogger(__name__)


# Read only the HTML <head> — the canonical link lives there. A 32KB cap is
# generous: Fern/Mintlify ship larger heads than Docusaurus, but 32KB is
# enough for every real-world docs site we've measured.
_READ_BYTE_CAP = 32 * 1024
_DEFAULT_TIMEOUT_S = 5.0
_DEFAULT_MAX_CONCURRENT = 5

# Regex matches both <link rel="canonical" href="..."> and the reversed
# attribute order. Case-insensitive because some CMSes emit REL="Canonical".
# Quote character may be " or '.
_CANONICAL_RE = re.compile(
    r'<link\s+[^>]*rel\s*=\s*["\']canonical["\'][^>]*>',
    re.IGNORECASE,
)
_HREF_RE = re.compile(
    r'href\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Fallback: <meta name="canonical" content="...">  — some Mintlify sites ship
# both. We prefer <link rel="canonical"> but accept <meta name="canonical">
# if the link form is absent.
_META_CANONICAL_RE = re.compile(
    r'<meta\s+[^>]*name\s*=\s*["\']canonical["\'][^>]*>',
    re.IGNORECASE,
)
_CONTENT_RE = re.compile(
    r'content\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _extract_canonical(html: str, base_url: str) -> Optional[str]:
    """
    Parse the first <link rel="canonical"> (or <meta name="canonical"> as
    fallback) from an HTML snippet. Returns the absolute URL or None.
    """
    m = _CANONICAL_RE.search(html)
    if m:
        href_m = _HREF_RE.search(m.group(0))
        if href_m:
            return urljoin(base_url, href_m.group(1).strip())
    m = _META_CANONICAL_RE.search(html)
    if m:
        content_m = _CONTENT_RE.search(m.group(0))
        if content_m:
            return urljoin(base_url, content_m.group(1).strip())
    return None


def _is_httpish(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _same_url(a: str, b: str) -> bool:
    """
    Compare URLs for normalization purposes: scheme + netloc (case-insensitive)
    + path (trailing slash stripped) + query. Fragments always ignored.
    """
    pa, pb = urlparse(a), urlparse(b)
    return (
        pa.scheme == pb.scheme
        and pa.netloc.lower() == pb.netloc.lower()
        and pa.path.rstrip("/") == pb.path.rstrip("/")
        and pa.query == pb.query
    )


async def _resolve_one(
    client: httpx.AsyncClient,
    hit: SearchHit,
    sem: asyncio.Semaphore) -> SearchHit:
    """
    Return a new SearchHit with `.url` replaced by the publisher-declared
    canonical. If normalization fails, returns the original hit unchanged.
    """
    async with sem:
        try:
            r = await client.get(hit.url, follow_redirects = True)
        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.debug(f"[canonical] {hit.url}: fetch failed ({e}); keeping original")
            return hit

    final_url = str(r.url)
    if r.status_code >= 400:
        logger.debug(
            f"[canonical] {hit.url}: HTTP {r.status_code}; keeping original"
        )
        return hit

    # Only parse canonical tag for HTML responses. Non-HTML (PDF, JSON, etc.)
    # are valid docs targets but don't have canonical link tags.
    content_type = (r.headers.get("content-type") or "").lower()
    canonical: Optional[str] = None
    if "html" in content_type:
        # Cap the HTML we parse — head is what matters.
        body = r.text[:_READ_BYTE_CAP] if len(r.text) > _READ_BYTE_CAP else r.text
        canonical = _extract_canonical(body, final_url)
        if canonical and not _is_httpish(canonical):
            canonical = None  # guard against javascript:, mailto:, etc.

    # Priority: canonical link > final (redirected) URL > original.
    chosen = canonical or final_url

    if _same_url(chosen, hit.url):
        return hit

    logger.info(
        f"[canonical] {hit.url} → {chosen} "
        f"({'meta' if canonical else 'redirect'})"
    )
    return SearchHit(
        url = chosen,
        title = hit.title,
        snippet = hit.snippet,
        engine = hit.engine,
    )


async def normalize_candidates(
    hits: list[SearchHit],
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_concurrent: int = _DEFAULT_MAX_CONCURRENT) -> list[SearchHit]:
    """
    Normalize each hit to its publisher-declared canonical URL, then dedupe.

    Dedup rule: first occurrence wins (providers return in relevance order).
    Two hits collapse when their canonical forms match per `_same_url`.
    """
    if not hits:
        return hits

    sem = asyncio.Semaphore(max_concurrent)
    timeout = httpx.Timeout(timeout_s, connect = 3.0)
    headers = {
        "User-Agent": "COELHONexus-KD-Resolver/1.0 (+https://rafaelcoelho1409.github.io)",
        "Accept": "text/html,application/xhtml+xml",
        # Legacy hosts sometimes gate redirects on a "browser-like" UA; the
        # httpx default UA gets 403 on a few Cloudflare-protected docs sites.
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(
        timeout = timeout,
        headers = headers,
        http2 = False,
    ) as client:
        tasks = [_resolve_one(client, h, sem) for h in hits]
        resolved = await asyncio.gather(*tasks, return_exceptions = False)

    # Dedupe — preserve order.
    seen: list[str] = []
    kept: list[SearchHit] = []
    dropped = 0
    for h in resolved:
        if any(_same_url(h.url, s) for s in seen):
            dropped += 1
            continue
        seen.append(h.url)
        kept.append(h)

    if dropped:
        logger.info(
            f"[canonical] normalized {len(hits)} hits → {len(kept)} "
            f"(dropped {dropped} duplicates after canonicalization)"
        )
    return kept
