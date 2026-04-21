"""
Knowledge Distiller — SearXNG Client (Stage B of the resolver)

Parallel query runner. Fires N query templates against the cluster's SearXNG
instance, aggregates + dedupes by URL, filters known-bad hosts, caps the
result set. Returns a flat list[SearxngHit] ranked by engine order.

Why 3 query templates instead of 1?
  SearXNG aggregates engines (Google, Bing, DDG, Brave, …) but the *query
  wording* still matters. A single "{name} documentation" misses sites that
  match "{name} getting started" or "{name} docs site:*.io". Fanning 3
  parallel queries costs ~500ms total (same as one) and surfaces ~2x more
  distinct canonical URLs.

The caller (docs_resolver.py) feeds these hits to the LLM rerank pass which
picks the canonical docs_url. We don't score here — that's the LLM's job.
"""
import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx

from schemas.knowledge.resolver import SearxngHit


logger = logging.getLogger(__name__)


_TIMEOUT_SECONDS = 8.0
_MAX_HITS_PER_QUERY = 10
_MAX_HITS_TOTAL = 30


# Hosts that are never canonical docs — drop pre-rerank so the LLM's
# context window isn't burned scoring garbage candidates.
_BAD_HOSTS = {
    "github.com",             # repo README != docs root (but we keep via registry_hint)
    "gitlab.com",
    "bitbucket.org",
    "stackoverflow.com",
    "reddit.com",
    "news.ycombinator.com",
    "en.wikipedia.org",
    "wikipedia.org",
    "youtube.com",
    "medium.com",
    "dev.to",
    "hashnode.com",
    "substack.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "linkedin.com",
    "pypi.org",               # registry page != docs
    "npmjs.com",
    "crates.io",
    "rubygems.org",
}


def _searxng_url() -> str:
    return os.environ.get(
        "SEARXNG_URL",
        "http://searxng.searxng.svc.cluster.local:8080",
    )


def _is_bad_host(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if host in _BAD_HOSTS:
        return True
    if host.startswith("www."):
        return host[4:] in _BAD_HOSTS
    return False


def _build_queries(framework: str, aliases: list[str], version: str | None) -> list[str]:
    """
    Three parallel query templates. Keep them explicit — heuristics like
    `site:docs.*.io` surface canonical subdomains that plain queries miss.
    """
    name = framework.strip()
    ver = f" {version}" if version else ""
    alias_clause = ""
    if aliases:
        # OR-join up to 2 aliases so SearXNG engines pick up synonyms
        alias_clause = " OR " + " OR ".join(f'"{a}"' for a in aliases[:2])
    return [
        f'"{name}"{alias_clause}{ver} official documentation',
        f'"{name}"{ver} docs (site:*.io OR site:*.dev OR site:*.com OR site:*.org)',
        f'"{name}"{ver} getting started tutorial',
    ]


async def _run_one_query(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str) -> list[SearxngHit]:
    params = {
        "q": query,
        "format": "json",
        "safesearch": "0",
        "language": "auto",
        "categories": "general",
    }
    try:
        resp = await client.get(endpoint, params = params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"[searxng] query failed ({query!r}): {e}")
        return []
    hits: list[SearxngHit] = []
    for entry in (data.get("results") or [])[:_MAX_HITS_PER_QUERY]:
        u = entry.get("url") or ""
        if not u.startswith(("http://", "https://")):
            continue
        if _is_bad_host(u):
            continue
        hits.append(SearxngHit(
            url = u,
            title = (entry.get("title") or "").strip(),
            snippet = (entry.get("content") or "").strip(),
            engine = (entry.get("engine") or "").strip(),
        ))
    return hits


def _dedupe_hits(hits: list[SearxngHit]) -> list[SearxngHit]:
    """Preserve first-seen URL; engine order is the tiebreaker."""
    seen: set[str] = set()
    out: list[SearxngHit] = []
    for h in hits:
        # Normalize: strip trailing slash + fragment for dedup
        key = h.url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


async def search_candidates(
    framework: str,
    aliases: list[str] | None = None,
    version: str | None = None,
    searxng_url: str | None = None) -> list[SearxngHit]:
    """
    Fire 3 parallel query templates against SearXNG, dedupe, return hits.
    Never raises — SearXNG outage returns an empty list and lets the resolver
    decide how to degrade (typically: fall back to registry-only hints).
    """
    aliases = aliases or []
    base = (searxng_url or _searxng_url()).rstrip("/")
    endpoint = f"{base}/search"
    queries = _build_queries(framework, aliases, version)

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(_TIMEOUT_SECONDS, connect = 5.0),
        headers = {"User-Agent": "COELHONexus-KD-Resolver/1.0"},
    ) as client:
        results = await asyncio.gather(
            *(_run_one_query(client, endpoint, q) for q in queries),
            return_exceptions = True,
        )

    merged: list[SearxngHit] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        merged.extend(r)
    deduped = _dedupe_hits(merged)[:_MAX_HITS_TOTAL]
    logger.info(
        f"[searxng] {framework!r} → {len(deduped)} deduped hits "
        f"from {len(queries)} queries"
    )
    return deduped
