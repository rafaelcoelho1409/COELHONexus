"""
Central Search API Fallback Chain

ONE source of truth for the ordered multi-provider web-search fallback used
by the Knowledge Distiller resolver (Stage B — candidate URLs). Mirrors the
llm_chain.py pattern: a single factory returns one primary + N fallbacks
that cascade on exceptions (rate-limit, quota-exhausted, 5xx, timeout).

ORDERING (April 2026 — re-review every quarter):
  1. Exa    — 100% recall@5 on our docs test (see scripts/test_exa_search.py).
              Independent neural+keyword index (not a Google/Bing wrapper).
              1,000 searches/mo free, no credit card. Sub-1s latency.
  2. Tavily — Agent-native, clean JSON response, curated independent index.
              1,000 searches/mo free, no credit card. Slightly broader index
              than Exa for non-docs queries.
  3. Jina   — Search API returning cleaned markdown per hit. 10M free tokens
              on signup (one-time, ~2,000 queries). No credit card.

EXCLUDED PROVIDERS:
  - Brave  — killed its free API tier on Feb 12, 2026 (now metered billing,
             $5/mo free credit but credit card required with no spending cap).
             Not strictly free; excluded from this chain.
  - Bing   — discontinued free tier in 2025.
  - Google CSE — 100/day free but requires Programmable Search Engine setup
             with per-site allowlist (too much friction for a generic docs
             resolver).
  - SearXNG — shared upstream engine throttling makes it unreliable under
             burst; kept as legacy reference only.

PROVIDER INTERLEAVING: each provider runs on a separate infrastructure with
independent rate-limit state. A single-provider outage (Exa down, Tavily
throttled, Jina token-depleted) leaves working options one step away.

FAILURE TAXONOMY (what each exception means → what the chain does):
  - ProviderQuotaExhausted  → cooldown 24h, cascade
  - ProviderRateLimited     → cooldown `retry_after`s, cascade
  - ProviderUnavailable     → cooldown 60s, cascade (5xx / timeout)
  - ProviderAuthError       → cooldown 24h, cascade (401/403 — key dead)
  - empty results           → cascade without cooldown (don't charge quota)
  - anything else           → cascade, log as unexpected

COOLDOWN STATE: in-process per SearchFallbackChain instance. One Celery
worker handles each resolver call; the 3-query burst in a resolver call
benefits from in-process coordination (2nd query skips a provider that the
1st query tripped). Cross-pod state would require Redis — future work.

ENV VARS:
  EXA_API_KEY     — required for Exa provider (skipped otherwise)
  TAVILY_API_KEY  — required for Tavily provider
  JINA_API_KEY    — required for Jina provider

Missing keys: the provider is SKIPPED (not added to the chain). At least
one must be set; otherwise `build_search_fallback_chain()` raises at
construction time — fail fast.

Reference: docs/KNOWLEDGE-DISTILLER-RESOLVER-STRATEGY.md Stage B.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, quote as _urlquote
import httpx

from schemas.knowledge.resolver import SearxngHit


logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions — failure taxonomy
# =============================================================================
class ProviderError(Exception):
    """Base for all provider-level failures routed by the fallback chain."""


class ProviderQuotaExhausted(ProviderError):
    """Monthly / lifetime quota hit — cascade and cool down for a day."""


class ProviderRateLimited(ProviderError):
    """Transient 429 throttle. `retry_after` seconds if the server announced one."""
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderUnavailable(ProviderError):
    """5xx, network error, or timeout. Cooldown ~1 min then retry later."""


class ProviderAuthError(ProviderError):
    """401 / 403 — key is invalid or revoked. Long cooldown, operator action required."""


# =============================================================================
# Canonical return shape
# =============================================================================
# Reuse SearxngHit from the resolver schemas — the field `engine` carries the
# provider name ("exa", "tavily", "jina", "brave"). Renaming is a separate
# cleanup once SearXNG is fully ripped out.


# =============================================================================
# Hosts never worth surfacing — filtered at the adapter boundary, pre-LLM
# =============================================================================
_BAD_HOSTS = {
    "github.com",          # repo page ≠ docs root (resolver upgrades separately)
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
    "pypi.org",
    "npmjs.com",
    "crates.io",
    "rubygems.org",
}


def _is_bad_host(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if host in _BAD_HOSTS:
        return True
    if host.startswith("www."):
        return host[4:] in _BAD_HOSTS
    return False


# =============================================================================
# Provider adapters — each normalizes its API response into list[SearxngHit]
# =============================================================================
@dataclass
class _BaseProvider:
    """Base for all provider adapters — async search + standardized errors."""
    name: str
    api_key: str
    timeout_s: float

    async def asearch(self, query: str, num_results: int = 10) -> list[SearxngHit]:
        raise NotImplementedError


class ExaProvider(_BaseProvider):
    """Exa Search API — POST https://api.exa.ai/search (keyword mode best for docs)."""
    URL = "https://api.exa.ai/search"

    def __init__(self, api_key: str, timeout_s: float = 20.0):
        super().__init__(name = "exa", api_key = api_key, timeout_s = timeout_s)

    async def asearch(self, query: str, num_results: int = 10) -> list[SearxngHit]:
        payload = {
            "query": query,
            "numResults": num_results,
            "type": "keyword",              # our test: 100% recall@5 for docs
            "contents": {"text": False},    # URL + title is enough for LLM rerank
        }
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout = self.timeout_s) as c:
                r = await c.post(self.URL, json = payload, headers = headers)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise ProviderUnavailable(f"exa network: {e}") from e
        self._raise_for_status(r)
        data = r.json()
        return self._normalize(data.get("results", []))

    def _normalize(self, results: list[dict]) -> list[SearxngHit]:
        hits: list[SearxngHit] = []
        for h in results:
            url = h.get("url") or ""
            if not url.startswith(("http://", "https://")) or _is_bad_host(url):
                continue
            hits.append(SearxngHit(
                url = url,
                title = (h.get("title") or "").strip(),
                snippet = (h.get("text") or "").strip()[:500],
                engine = self.name,
            ))
        return hits

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code == 429:
            raise ProviderRateLimited(
                f"exa 429: {r.text[:200]}",
                retry_after = _parse_retry_after(r),
            )
        if r.status_code in (401, 403):
            raise ProviderAuthError(f"exa auth: {r.status_code}")
        if r.status_code == 402:
            raise ProviderQuotaExhausted(f"exa 402: {r.text[:200]}")
        if 500 <= r.status_code < 600:
            raise ProviderUnavailable(f"exa {r.status_code}")
        if r.status_code >= 400:
            raise ProviderError(f"exa {r.status_code}: {r.text[:200]}")


class TavilyProvider(_BaseProvider):
    """Tavily Search API — POST https://api.tavily.com/search (JSON in/out)."""
    URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str, timeout_s: float = 20.0):
        super().__init__(name = "tavily", api_key = api_key, timeout_s = timeout_s)

    async def asearch(self, query: str, num_results: int = 10) -> list[SearxngHit]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": num_results,
            "search_depth": "basic",
            "include_answer": False,         # don't want Tavily's summary
            "include_raw_content": False,
        }
        try:
            async with httpx.AsyncClient(timeout = self.timeout_s) as c:
                r = await c.post(self.URL, json = payload)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise ProviderUnavailable(f"tavily network: {e}") from e
        self._raise_for_status(r)
        data = r.json()
        return self._normalize(data.get("results", []))

    def _normalize(self, results: list[dict]) -> list[SearxngHit]:
        hits: list[SearxngHit] = []
        for h in results:
            url = h.get("url") or ""
            if not url.startswith(("http://", "https://")) or _is_bad_host(url):
                continue
            hits.append(SearxngHit(
                url = url,
                title = (h.get("title") or "").strip(),
                snippet = (h.get("content") or "").strip()[:500],
                engine = self.name,
            ))
        return hits

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code == 429:
            raise ProviderRateLimited(
                f"tavily 429: {r.text[:200]}",
                retry_after = _parse_retry_after(r),
            )
        if r.status_code in (401, 403):
            raise ProviderAuthError(f"tavily auth: {r.status_code}")
        if r.status_code == 402:
            raise ProviderQuotaExhausted(f"tavily 402: {r.text[:200]}")
        if 500 <= r.status_code < 600:
            raise ProviderUnavailable(f"tavily {r.status_code}")
        if r.status_code >= 400:
            raise ProviderError(f"tavily {r.status_code}: {r.text[:200]}")


class JinaProvider(_BaseProvider):
    """Jina Search — GET https://s.jina.ai/{query} (JSON via Accept header)."""
    URL = "https://s.jina.ai"

    def __init__(self, api_key: str, timeout_s: float = 30.0):
        super().__init__(name = "jina", api_key = api_key, timeout_s = timeout_s)

    async def asearch(self, query: str, num_results: int = 10) -> list[SearxngHit]:
        # Jina returns markdown by default; Accept: application/json gives
        # structured {data: [{url, title, description, content}]}.
        url = f"{self.URL}/{_urlquote(query, safe='')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "X-Retain-Images": "none",
            "X-No-Cache": "false",
        }
        try:
            async with httpx.AsyncClient(timeout = self.timeout_s) as c:
                r = await c.get(url, headers = headers)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise ProviderUnavailable(f"jina network: {e}") from e
        self._raise_for_status(r)
        data = r.json()
        return self._normalize(data.get("data", []), num_results)

    def _normalize(self, results: list[dict], num_results: int) -> list[SearxngHit]:
        hits: list[SearxngHit] = []
        for h in results[:num_results]:
            url = h.get("url") or ""
            if not url.startswith(("http://", "https://")) or _is_bad_host(url):
                continue
            hits.append(SearxngHit(
                url = url,
                title = (h.get("title") or "").strip(),
                # Jina returns cleaned markdown in `content`; keep the first chunk
                # as a snippet so the LLM rerank sees context, not the whole page.
                snippet = (h.get("description") or h.get("content") or "").strip()[:500],
                engine = self.name,
            ))
        return hits

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.status_code == 429:
            raise ProviderRateLimited(
                f"jina 429: {r.text[:200]}",
                retry_after = _parse_retry_after(r),
            )
        if r.status_code in (401, 403):
            raise ProviderAuthError(f"jina auth: {r.status_code}")
        if r.status_code == 402:
            raise ProviderQuotaExhausted(f"jina 402: {r.text[:200]}")
        if 500 <= r.status_code < 600:
            raise ProviderUnavailable(f"jina {r.status_code}")
        if r.status_code >= 400:
            raise ProviderError(f"jina {r.status_code}: {r.text[:200]}")


# =============================================================================
# Helpers
# =============================================================================
def _parse_retry_after(r: httpx.Response) -> float | None:
    """Parse a Retry-After header (seconds or HTTP-date). Return None if absent."""
    v = r.headers.get("Retry-After")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        # HTTP-date format — treat as unknown, default to 30s
        return 30.0


# =============================================================================
# Fallback chain — cascade with per-provider cooldown
# =============================================================================
class SearchFallbackChain:
    """
    Wraps N providers in priority order. `asearch(query)` walks them until
    one returns a non-empty result set; on provider error, marks a cooldown
    and cascades.

    In-process cooldown state is shared across `asearch` calls on the same
    instance — important for the resolver's 3-query burst: if query #1
    trips Exa's rate limit, queries #2 and #3 skip Exa for the cooldown
    window instead of burning latency on three 429 round-trips.
    """
    def __init__(self, providers: list[_BaseProvider]):
        if not providers:
            raise RuntimeError("SearchFallbackChain needs ≥1 provider")
        self.providers = providers
        self._cooldown_until: dict[str, float] = {}

    async def asearch(
        self,
        query: str,
        num_results: int = 10) -> list[SearxngHit]:
        """
        Return the first non-empty hit list from any provider. Empty results
        are treated as a soft miss — cascade without cooldown. All-empty
        cascade returns []; all-errored cascade raises RuntimeError.
        """
        now = time.monotonic()
        last_error: Exception | None = None
        tried: list[str] = []
        for p in self.providers:
            cooldown = self._cooldown_until.get(p.name, 0.0)
            if cooldown > now:
                logger.info(
                    f"[search] skip {p.name} "
                    f"(cooldown {cooldown - now:.1f}s remaining)"
                )
                continue
            tried.append(p.name)
            try:
                hits = await p.asearch(query, num_results = num_results)
            except ProviderQuotaExhausted as e:
                self._cooldown_until[p.name] = now + 24 * 3600
                logger.warning(f"[search] {p.name} quota exhausted — 24h cooldown: {e}")
                last_error = e
                continue
            except ProviderAuthError as e:
                self._cooldown_until[p.name] = now + 24 * 3600
                logger.error(f"[search] {p.name} auth failure — key dead: {e}")
                last_error = e
                continue
            except ProviderRateLimited as e:
                wait = e.retry_after or 30.0
                self._cooldown_until[p.name] = now + wait
                logger.info(f"[search] {p.name} rate-limited — {wait}s cooldown: {e}")
                last_error = e
                continue
            except ProviderUnavailable as e:
                self._cooldown_until[p.name] = now + 60
                logger.info(f"[search] {p.name} unavailable — 60s cooldown: {e}")
                last_error = e
                continue
            except ProviderError as e:
                logger.info(f"[search] {p.name} error (no cooldown): {e}")
                last_error = e
                continue
            if hits:
                logger.info(f"[search] {p.name} OK — {len(hits)} hits for {query!r}")
                return hits
            logger.info(f"[search] {p.name} returned 0 hits — cascading")
        if last_error is None:
            return []  # all providers returned empty, none errored
        raise RuntimeError(
            f"all search providers failed (tried {tried}): {type(last_error).__name__}: {last_error}"
        )


# =============================================================================
# Factory
# =============================================================================
def build_search_fallback_chain(
    exa_timeout_s: float = 20.0,
    tavily_timeout_s: float = 20.0,
    jina_timeout_s: float = 30.0) -> SearchFallbackChain:
    """
    Assemble the ordered provider list from env-var keys. Each provider is
    OPTIONAL — a missing key skips that provider entirely, so a dev with
    only Exa configured can still run the resolver.

    Raises RuntimeError if ZERO providers are configured.
    """
    providers: list[_BaseProvider] = []

    exa_key = os.environ.get("EXA_API_KEY", "").strip()
    if exa_key:
        providers.append(ExaProvider(exa_key, timeout_s = exa_timeout_s))

    tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if tavily_key:
        providers.append(TavilyProvider(tavily_key, timeout_s = tavily_timeout_s))

    jina_key = os.environ.get("JINA_API_KEY", "").strip()
    if jina_key:
        providers.append(JinaProvider(jina_key, timeout_s = jina_timeout_s))

    if not providers:
        raise RuntimeError(
            "No search provider keys configured. Set at least one of: "
            "EXA_API_KEY, TAVILY_API_KEY, JINA_API_KEY."
        )

    logger.info(
        f"[search] fallback chain built: {[p.name for p in providers]}"
    )
    return SearchFallbackChain(providers)


# =============================================================================
# Query template — single-query + provider fallback (quota-frugal)
# =============================================================================
_NUM_RESULTS = 10


def _build_query(framework: str, aliases: list[str], version: str | None) -> str:
    """
    ONE query template per framework — not three. Free-tier quotas are
    scarce (Exa 1K/mo, Tavily 1K/mo, Jina 10M tokens), so we minimize
    calls: spend one query per topic, cascade across providers only on
    error or empty results.

    Our Exa test showed the canonical URL at position #1 from this single
    template in ~90% of docs queries. The LLM rerank handles the remaining
    10% by picking from the 10 hits returned. Multiple query templates
    would triple the quota cost for a marginal recall improvement.
    """
    name = framework.strip()
    ver = f" {version}" if version else ""
    alias_clause = ""
    if aliases:
        alias_clause = " OR " + " OR ".join(f'"{a}"' for a in aliases[:2])
    return f'"{name}"{alias_clause}{ver} official documentation'


async def search_candidates(
    chain: SearchFallbackChain,
    framework: str,
    aliases: list[str] | None = None,
    version: str | None = None) -> list[SearxngHit]:
    """
    Run ONE search query through the fallback chain. Primary provider (Exa)
    handles the call; fallback cascade triggers only on error / empty result.

    Quota cost per call = 1 query on the first healthy provider, NOT 3.

    The chain returns on the first non-empty result set from any provider.
    In-process cooldown state across calls means a rate-limited primary is
    auto-skipped on subsequent calls for `retry_after` seconds.
    """
    aliases = aliases or []
    query = _build_query(framework, aliases, version)
    hits = await chain.asearch(query, num_results = _NUM_RESULTS)
    logger.info(
        f"[search] {framework!r} → {len(hits)} hits "
        f"(provider={hits[0].engine if hits else 'none'})"
    )
    return hits
